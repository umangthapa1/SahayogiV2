import sys
import speech_recognition as sr
import datetime
import webbrowser
import os
import pywhatkit
import pyjokes
import threading
import queue
import requests
from bs4 import BeautifulSoup
import json
import glob
import re
import time
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import QSystemTrayIcon
import subprocess
from ctypes import *
from ollama import chat, generate

tray_icon= None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
ASSET_SEARCH_PATHS = [
    os.path.join(BASE_DIR, "assets"),
    os.path.join(PROJECT_ROOT, "assets"),
    BASE_DIR,
]


def asset_path(filename, fallback=None):
    candidates = [filename]
    if fallback:
        candidates.append(fallback)
    for name in candidates:
        for root in ASSET_SEARCH_PATHS:
            candidate = os.path.join(root, name)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(ASSET_SEARCH_PATHS[0], filename)


class TerminalOutputProxy:
    def __init__(self, ui_ref):
        self._ui_ref = ui_ref

    def appendPlainText(self, text):
        self._ui_ref.append_terminal_log(str(text), role="assistant")


class WebBridge(QtCore.QObject):
    commandReceived = QtCore.pyqtSignal(str)
    languageChanged = QtCore.pyqtSignal(str)

    @QtCore.pyqtSlot(str)
    def submitCommand(self, command):
        cleaned = (command or "").strip()
        if cleaned:
            self.commandReceived.emit(cleaned)

    @QtCore.pyqtSlot(str)
    def setLanguage(self, language_code):
        cleaned = (language_code or "").strip().lower()
        if cleaned:
            self.languageChanged.emit(cleaned)


class Ui_Dialog(QtCore.QObject):
    stateChanged = QtCore.pyqtSignal(str)
    logAppended = QtCore.pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._page_ready = False
        self._pending_logs = []
        self._pending_state = "neutral"
        self.terminalOutputBox = TerminalOutputProxy(self)
        self.web_view = None
        self.channel = None
        self.bridge = None

    def setupUi(self, Dialog):
        Dialog.setObjectName("Dialog")
        Dialog.resize(1280, 760)
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(asset_path("logo.png")), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        Dialog.setWindowIcon(icon)
        Dialog.setAutoFillBackground(False)

        layout = QtWidgets.QVBoxLayout(Dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.web_view = QWebEngineView(Dialog)
        self.web_view.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        layout.addWidget(self.web_view)

        self.bridge = WebBridge()
        self.bridge.commandReceived.connect(self._on_typed_command)
        self.bridge.languageChanged.connect(self._on_language_changed)

        self.channel = QWebChannel(self.web_view.page())
        self.channel.registerObject("sahayogiBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.stateChanged.connect(self._apply_state)
        self.logAppended.connect(self._apply_log)
        self.web_view.loadFinished.connect(self._on_page_loaded)

        ui_html_path = os.path.join(BASE_DIR, "ui.html")
        self.web_view.load(QtCore.QUrl.fromLocalFile(ui_html_path))

        self.retranslateUi(Dialog)
        QtCore.QMetaObject.connectSlotsByName(Dialog)

    def retranslateUi(self, Dialog):
        _translate = QtCore.QCoreApplication.translate
        Dialog.setWindowTitle(_translate("Dialog", "S.A.H.A.Y.O.G.I"))

    def updateui(self, state):
        self.stateChanged.emit(state or "neutral")

    def append_terminal_log(self, text, role="assistant"):
        self.logAppended.emit(str(text), role)

    @QtCore.pyqtSlot(bool)
    def _on_page_loaded(self, ok):
        self._page_ready = bool(ok)
        if not self._page_ready:
            print("ui.html load गर्न सकिएन।")
            return
        self._run_js("window.sahayogiClearDemoLogs && window.sahayogiClearDemoLogs();")
        current_lang_payload = json.dumps(CURRENT_LANGUAGE)
        self._run_js(f"window.sahayogiSyncLanguage && window.sahayogiSyncLanguage({current_lang_payload});")
        self._run_js("window.sahayogiMarkReady && window.sahayogiMarkReady();")
        pending_logs = list(self._pending_logs)
        self._pending_logs.clear()
        self._apply_state(self._pending_state)
        for text, role in pending_logs:
            self._apply_log(text, role)

    @QtCore.pyqtSlot(str)
    def _apply_state(self, state):
        self._pending_state = state
        if not self._page_ready:
            return
        state_payload = json.dumps(state)
        self._run_js(f"window.sahayogiSetState && window.sahayogiSetState({state_payload});")

    @QtCore.pyqtSlot(str, str)
    def _apply_log(self, text, role):
        if not self._page_ready:
            self._pending_logs.append((text, role))
            return
        text_payload = json.dumps(text, ensure_ascii=False)
        role_payload = json.dumps(role)
        self._run_js(
            f"window.sahayogiAppendLog && window.sahayogiAppendLog({text_payload}, {role_payload});"
        )

    @QtCore.pyqtSlot(str)
    def _on_typed_command(self, command):
        self.append_terminal_log(command, role="user")
        worker = threading.Thread(
            target=handle_command,
            args=(command, self.terminalOutputBox),
            daemon=True,
        )
        worker.start()

    @QtCore.pyqtSlot(str)
    def _on_language_changed(self, language_code):
        selected = set_runtime_language(language_code)
        label = LANGUAGE_SETTINGS[selected]["label"]
        self.append_terminal_log(
            f"Language switched to {label}. Voice: {os.path.basename(PIPER_MODEL)}",
            role="system",
        )
        self.sync_language(selected)

    def sync_language(self, language_code):
        lang_payload = json.dumps(language_code)
        self._run_js(f"window.sahayogiSyncLanguage && window.sahayogiSyncLanguage({lang_payload});")

    def _run_js(self, code):
        if self.web_view is None:
            return
        try:
            self.web_view.page().runJavaScript(code)
        except Exception as e:
            print(f"UI JS त्रुटि: {e}")

recognizer=sr.Recognizer()

OLLAMA_MODEL = "gemma3:4b"
MAX_CHAT_HISTORY = 24
chat_history = []

LANGUAGE_SETTINGS = {
    "en": {
        "label": "English",
        "speech_lang": "en-US",
        "listen_text": "Listening...",
        "recognizing_text": "Recognizing...",
        "wake_ack": "Yes, how can I help?",
        "goodbye_text": "Goodbye! See you soon!",
        "greeting": {
            "morning": "Good morning!",
            "afternoon": "Good afternoon!",
            "evening": "Good evening!",
            "intro": "The time is {time}. I am Sahayogi. How can I assist you?",
        },
        "system_prompt": (
            "You are Sahayogi, a practical desktop AI assistant. "
            "Hold natural casual conversations, answer follow-up questions, and keep context across turns. "
            "Be friendly but not cheesy. Keep responses concise unless asked for detail. "
            "When the user asks for actions, provide direct help. "
            "Do not greet every reply and do not repeat your name unless asked."
        ),
        "code_html_prompt": (
            "{query}. Return only valid HTML code. No markdown, no backticks, no explanation."
        ),
        "code_python_prompt": (
            "{query}. Return only valid Python code. No markdown, no backticks, no explanation."
        ),
        "self_info_prompt": (
            "You are Sahayogi, a voice assistant powered by Gemma 3. "
            "Briefly explain who you are and what you can do in English."
        ),
        "piper_models": [
            "~/piper_models/en_US-amy-medium.onnx",
            "~/piper_models/voice.onnx",
            "~/piper_models/en_*.onnx",
        ],
    },
    "ne": {
        "label": "नेपाली",
        "speech_lang": "ne-NP",
        "listen_text": "सुनिरहेको छु...",
        "recognizing_text": "बुझ्दै छु...",
        "wake_ack": "हो, मैले कसरी मद्दत गर्न सक्छु?",
        "goodbye_text": "अलविदा! फेरि भेटौला!",
        "greeting": {
            "morning": "शुभ प्रभात!",
            "afternoon": "शुभ दोपहर!",
            "evening": "शुभ संध्या!",
            "intro": "समय {time} छ। म सहायोगी हुँ। मैले तपाईंलाई कसरी मद्दत गर्न सक्छु?",
        },
        "system_prompt": (
            "तपाईं सहायोगी नामको व्यावहारिक डेस्कटप सहायक हुनुहुन्छ। "
            "प्रयोगकर्तासँग स्वाभाविक तरिकाले कुराकानी गर्नुहोस्, follow-up प्रश्नलाई पनि बुझ्नुहोस्, "
            "र उत्तर स्पष्ट तर छोटो राख्नुहोस्। इमोजी प्रयोग नगर्नुहोस्। "
            "हरेक उत्तरमा अभिवादन नदोहोर्याउनुहोस्, नाम पनि बारम्बार नभन्नुहोस्।"
        ),
        "code_html_prompt": (
            "{query}। केवल HTML कोड मात्र दिनुहोस्। कुनै markdown, backticks वा व्याख्या नदिनुहोस्।"
        ),
        "code_python_prompt": (
            "{query}। केवल Python कोड मात्र दिनुहोस्। कुनै markdown, backticks वा व्याख्या नदिनुहोस्।"
        ),
        "self_info_prompt": (
            "तपाईं सहायोगी नामको भ्वाइस असिस्ट्यान्ट हुनुहुन्छ, Gemma 3 द्वारा संचालित। "
            "आफ्नो बारेमा छोटकरीमा नेपालीमा बताउनुहोस्।"
        ),
        "piper_models": [
            "~/piper_models/ne_NP-chitwan-medium.onnx",
            "~/piper_models/voice.onnx",
            "~/piper_models/ne_*.onnx",
        ],
    },
}

DEFAULT_LANGUAGE = "en"
CURRENT_LANGUAGE = DEFAULT_LANGUAGE

# News cache for live knowledge
NEWS_CACHE_FILE = os.path.join(BASE_DIR, "news_cache.txt")
current_news_context = ""

# Topic-specific search queries for Google News
TOPIC_QUERIES = {
    "f1": "https://news.google.com/rss/search?q=Formula%201%20F1&hl=en-US&gl=US",
    "football": "https://news.google.com/rss/search?q=football%20soccer&hl=en-US&gl=US",
    "cricket": "https://news.google.com/rss/search?q=cricket&hl=en-US&gl=US",
    "tennis": "https://news.google.com/rss/search?q=tennis&hl=en-US&gl=US",
    "tech": "https://news.google.com/rss/search?q=technology&hl=en-US&gl=US",
    "ai": "https://news.google.com/rss/search?q=artificial%20intelligence&hl=en-US&gl=US",
    "business": "https://news.google.com/rss/search?q=business%20economy&hl=en-US&gl=US",
    "science": "https://news.google.com/rss/search?q=science&hl=en-US&gl=US",
    "politics": "https://news.google.com/rss/search?q=politics&hl=en-US&gl=US",
    "health": "https://news.google.com/rss/search?q=health%20medical&hl=en-US&gl=US",
    "nepal": "https://news.google.com/rss/search?q=Nepal&hl=en-US&gl=US",
    "india": "https://news.google.com/rss/search?q=India&hl=en-US&gl=US",
    "usa": "https://news.google.com/rss/search?q=United%20States&hl=en-US&gl=US",
    "uk": "https://news.google.com/rss/search?q=United%20Kingdom&hl=en-US&gl=US",
}

topic_news_cache = {}

def fetch_latest_news():
    """Fetch latest news headlines from Google News RSS feed and save to cache."""
    global current_news_context
    
    # Google News RSS feed for top stories
    rss_url = "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US"
    
    # Suppress XML parser warning when using HTML parser on XML documents
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        items = soup.find_all('item')[:5]  # Top 5 headlines
        
        headlines = []
        for item in items:
            title = item.find('title')
            if title and title.text:
                headlines.append(title.text.strip())
        
        if headlines:
            current_news_context = "\n".join(f"{i+1}. {headline}" for i, headline in enumerate(headlines))
            # Save to cache file
            with open(NEWS_CACHE_FILE, 'w', encoding='utf-8') as f:
                f.write(current_news_context)
            print(f"News updated: {len(headlines)} headlines cached")
        else:
            print("No headlines found in RSS feed")
            # Try to load from cache if fetch fails
            load_news_from_cache()
            
    except requests.RequestException as e:
        print(f"News fetch error: {e}")
        load_news_from_cache()
    except Exception as e:
        print(f"News processing error: {e}")
        load_news_from_cache()

def load_news_from_cache():
    """Load news from cached file if available."""
    global current_news_context
    try:
        if os.path.exists(NEWS_CACHE_FILE):
            with open(NEWS_CACHE_FILE, 'r', encoding='utf-8') as f:
                current_news_context = f.read().strip()
            print("Loaded news from cache")
    except Exception as e:
        print(f"Cache load error: {e}")
        current_news_context = ""

def fetch_topic_news(topic):
    """Fetch news for a specific topic (e.g., F1, football, tech)."""
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    
    topic_lower = topic.lower()
    
    # Check if we have a direct RSS feed for this topic
    if topic_lower in TOPIC_QUERIES:
        rss_url = TOPIC_QUERIES[topic_lower]
    else:
        # Search for the topic
        rss_url = f"https://news.google.com/rss/search?q={topic.replace(' ', '%20')}&hl=en-US&gl=US"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        items = soup.find_all('item')[:10]
        
        headlines = []
        for item in items:
            title = item.find('title')
            if title and title.text:
                headlines.append(title.text.strip())
        
        if headlines:
            topic_news_cache[topic_lower] = "\n".join(f"{i+1}. {headline}" for i, headline in enumerate(headlines))
            return topic_news_cache[topic_lower]
        
    except Exception as e:
        print(f"Topic news fetch error: {e}")
    
    return None

def get_current_date():
    """Get current date formatted for the system prompt."""
    return datetime.datetime.now().strftime("%Y-%m-%d")

def get_live_system_prompt():
    """Get the conversation system prompt with live news context."""
    base_prompt = get_conversation_system_prompt()
    
    # Enhanced contextual prompt - but user controls when to use context
    contextual_prompt = """You are a context-aware AI assistant.

IMPORTANT: How to respond to news/topic questions:
1. When user asks for "news" or "what's happening" - give ONLY headlines/titles in a concise list
2. When user explicitly asks for "context", "background", "explain more", "tell me about" - then provide detailed context with background
3. When user asks about specific topics (F1, sports, tech, etc.) - provide answer + relevant context
4. Keep responses concise unless context is explicitly requested

Today is {date}. Here are the latest global news headlines:
{news}

Follow these rules when answering news questions.""".format(date=get_current_date(), news=current_news_context)
    
    return f"{contextual_prompt}\n\n{base_prompt}"


PIPER_MODEL = os.path.expanduser("~/piper_models/voice.onnx")
PIPER_PATH = os.path.expanduser("~/piper_models/piper")

recognizer.dynamic_energy_threshold = False
recognizer.energy_threshold = 350  

def load_dataset(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)


def get_lang_config():
    return LANGUAGE_SETTINGS.get(CURRENT_LANGUAGE, LANGUAGE_SETTINGS[DEFAULT_LANGUAGE])


def tr(en_text, ne_text):
    return en_text if CURRENT_LANGUAGE == "en" else ne_text

def resolve_piper_model_for_language(language_code):
    config = LANGUAGE_SETTINGS.get(language_code, LANGUAGE_SETTINGS[DEFAULT_LANGUAGE])
    candidates = config.get("piper_models", [])

    expanded_candidates = []
    for item in candidates:
        pattern = os.path.expanduser(item)
        if any(ch in pattern for ch in "*?[]"):
            expanded_candidates.extend(sorted(glob.glob(pattern)))
        else:
            expanded_candidates.append(pattern)

    for path in expanded_candidates:
        if os.path.isfile(path):
            return path
    return expanded_candidates[0] if expanded_candidates else os.path.expanduser("~/piper_models/voice.onnx")


def set_runtime_language(language_code):
    global CURRENT_LANGUAGE, PIPER_MODEL, chat_history

    requested = (language_code or "").strip().lower()
    if requested not in LANGUAGE_SETTINGS:
        requested = DEFAULT_LANGUAGE

    CURRENT_LANGUAGE = requested
    PIPER_MODEL = resolve_piper_model_for_language(requested)
    chat_history = []
    return CURRENT_LANGUAGE


def get_conversation_system_prompt():
    return get_lang_config()["system_prompt"]


def normalize_for_matching(text):
    value = (text or "").lower().strip()
    value = value.replace("’", "'").replace("`", "'").replace("'", "")
    value = re.sub(r"[^\w\s\u0900-\u097F]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_first_label(response_text, labels, default):
    normalized = (response_text or "").upper().replace("-", "_")
    normalized = re.sub(r"\s+", "_", normalized)
    for label in labels:
        if label in normalized:
            return label
    return default


def ai_pick_label(query, labels, instructions_en, instructions_ne, default):
    if CURRENT_LANGUAGE == "en":
        prompt = (
            f"{instructions_en}\n"
            f"Allowed labels: {labels}\n"
            "Return exactly one label and nothing else.\n"
            f'User text: "{query}"'
        )
    else:
        prompt = (
            f"{instructions_ne}\n"
            f"Allowed labels: {labels}\n"
            "ठ्याक्कै एउटा लेबल मात्र फर्काउनुहोस्।\n"
            f'प्रयोगकर्ताको पाठ: "{query}"'
        )

    try:
        result = run_ollama_chat([{"role": "user", "content": prompt}], model=OLLAMA_MODEL)
        return extract_first_label(result, labels, default)
    except Exception as e:
        print(f"AI label selection error: {e}")
        return default


def should_end_conversation(query):
    if not normalize_for_matching(query):
        return False

    label_primary = ai_pick_label(
        query=query,
        labels=["END_SESSION", "CONTINUE_SESSION"],
        instructions_en=(
            "Decide whether the user explicitly wants to stop the current conversation loop. "
            "Only choose END_SESSION for clear stop/finish/exit statements."
        ),
        instructions_ne=(
            "प्रयोगकर्ताले अहिलेको वार्तालाप लूप स्पष्ट रूपमा रोक्न चाहेको हो कि होइन निर्णय गर्नुहोस्। "
            "स्पष्ट रोक्ने/समाप्त गर्ने चाहना भए मात्र END_SESSION छान्नुहोस्।"
        ),
        default="CONTINUE_SESSION",
    )
    if label_primary != "END_SESSION":
        return False

    label_secondary = ai_pick_label(
        query=query,
        labels=["END_SESSION", "CONTINUE_SESSION"],
        instructions_en=(
            "Strictly detect conversation-ending intent. "
            "Do NOT choose END_SESSION for normal commands, yes/no answers, or follow-up requests."
        ),
        instructions_ne=(
            "कडा रूपमा वार्तालाप समाप्त गर्ने अभिप्राय पत्ता लगाउनुहोस्। "
            "सामान्य आदेश, yes/no उत्तर, वा follow-up अनुरोधमा END_SESSION नछान्नुहोस्।"
        ),
        default="CONTINUE_SESSION",
    )
    if label_secondary != "END_SESSION":
        return False

    # Guardrail: if this is likely an actionable command, keep the session alive.
    intent = get_smart_intent(query)
    return intent in {"CASUAL_CHAT", "SELF_INFO"}


def detect_language_switch_command(query):
    if not normalize_for_matching(query):
        return None

    # Only detect language switch if explicitly requested
    # Examples: "change language to English", "switch to Nepali", "अब नेपालीमा बोलौं"
    # Do NOT switch if user is just speaking in another language or asking questions
    label = ai_pick_label(
        query=query,
        labels=["LANG_SWITCH_EN", "LANG_SWITCH_NE", "NO_LANGUAGE_SWITCH"],
        instructions_en=(
            "Determine if the user is EXPLICITLY asking to change the assistant's language. "
            "Only return LANG_SWITCH_EN if they clearly say something like 'switch to English', 'change language', 'speak English'. "
            "Only return LANG_SWITCH_NE if they clearly say something like 'switch to Nepali', 'change to Nepali', 'now speak Nepali'. "
            "If they are just talking, asking questions, or saying anything else, return NO_LANGUAGE_SWITCH."
        ),
        instructions_ne=(
            "प्रयोगकर्ताले सहायकको भाषा परिवर्तन गर्न स्पष्ट रूपमा मागेको हो कि होइन निर्णय गर्नुहोस्। "
            "केवल अंग्रेजी चाहिएको हो भनेर स्पष्ट भनेमा मात्र LANG_SWITCH_EN फर्काउनुहोस् (जस्तै: 'अंग्रेजीमा बोल', 'भाषा बदल'). "
            "केवल नेपाली चाहिएको हो भनेर स्पष्ट भनेमा मात्र LANG_SWITCH_NE फर्काउनुहोस् (जस्तै: 'नेपालीमा बोल', 'अब नेपालीमा कुरा गरौं'). "
            "सामान्य कुराकानी, प्रश्न, वा अरु कुनै कुरा भएमा NO_LANGUAGE_SWITCH फर्काउनुहोस्।"
        ),
        default="NO_LANGUAGE_SWITCH",
    )

    if label == "LANG_SWITCH_EN":
        return "en"
    if label == "LANG_SWITCH_NE":
        return "ne"
    return None


def should_wake_from_utterance(query):
    if not normalize_for_matching(query):
        return False

    label = ai_pick_label(
        query=query,
        labels=["WAKE_UP", "IGNORE"],
        instructions_en=(
            "Decide if this utterance is intended to wake/invoke the assistant for a command."
            " If yes return WAKE_UP, otherwise IGNORE."
        ),
        instructions_ne=(
            "यो वाक्य सहायकलाई बोलाएर आदेश दिन सुरु गर्ने उद्देश्यले भनिएको हो कि होइन निर्णय गर्नुहोस्। "
            "हो भने WAKE_UP, होइन भने IGNORE।"
        ),
        default="IGNORE",
    )
    return label == "WAKE_UP"


def is_affirmative_response(query):
    if not normalize_for_matching(query):
        return False

    label = ai_pick_label(
        query=query,
        labels=["AFFIRMATIVE", "NEGATIVE"],
        instructions_en=(
            "Classify whether the user is clearly saying yes/confirm/approve."
            " Return AFFIRMATIVE or NEGATIVE."
        ),
        instructions_ne=(
            "प्रयोगकर्ताले स्पष्ट रूपमा हो/स्वीकृति दिएको हो कि होइन वर्गीकृत गर्नुहोस्। "
            "AFFIRMATIVE वा NEGATIVE फर्काउनुहोस्।"
        ),
        default="NEGATIVE",
    )
    return label == "AFFIRMATIVE"


def apply_language_change(language_code, outputterminalBox=None):
    selected = set_runtime_language(language_code)
    label = LANGUAGE_SETTINGS[selected]["label"]
    message = tr(
        f"Language switched to {label}.",
        f"भाषा {label} मा परिवर्तन भयो।",
    )

    if outputterminalBox is not None:
        outputterminalBox.appendPlainText(message)

    if "ui" in globals() and ui is not None:
        try:
            ui.sync_language(selected)
            ui.append_terminal_log(
                f"Voice model: {os.path.basename(PIPER_MODEL)}",
                role="system",
            )
        except Exception as e:
            print(f"Language UI sync error: {e}")

    speak(message)
    return selected

from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

def py_error_handler(filename, line, function, err, fmt):
    pass

ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    pass

SAFE_TERMINAL_COMMANDS = {
    "TERMINAL_SYSINFO": {
        "command": ["neofetch"],
        "announce": {
            "en": "Running system info command.",
            "ne": "सिस्टम जानकारी आदेश चलाउँदैछु।",
        },
    },
    "TERMINAL_CLEAR_CACHE": {
        "command": ["paccache", "-r"],
        "announce": {
            "en": "Cleaning old package cache.",
            "ne": "पुरानो प्याकेज क्यास सफा गर्दैछु।",
        },
    },
}

INTENT_LABELS = [
    "OPEN_YOUTUBE",
    "OPEN_GOOGLE",
    "OPEN_FACEBOOK",
    "TELL_JOKE",
    "GET_TIME",
    "PLAY_SONG",
    "VOLUME_UP",
    "VOLUME_DOWN",
    "SYSTEM_SHUTDOWN",
    "WEATHER_INFO",
    "OPEN_VSCODE",
    "CODE_HTML",
    "CODE_PYTHON",
    "SELF_INFO",
    "TERMINAL_COMMAND",
    "CASUAL_CHAT",
    "GENERAL_KNOWLEDGE",
]

# Initialize language-dependent runtime settings at startup.
set_runtime_language(DEFAULT_LANGUAGE)


def extract_chat_text(response):
    if isinstance(response, dict):
        return response.get("message", {}).get("content", "")
    message = getattr(response, "message", None)
    if message is None:
        return ""
    return getattr(message, "content", "")


def extract_generate_text(response):
    if isinstance(response, dict):
        return response.get("response", "")
    return getattr(response, "response", "")


def run_ollama_chat(messages, model=OLLAMA_MODEL):
    response = chat(model=model, messages=messages)
    return extract_chat_text(response).strip()


def run_ollama_chat_stream(messages, model=OLLAMA_MODEL):
    stream = chat(model=model, messages=messages, stream=True)
    for chunk in stream:
        text_chunk = extract_chat_text(chunk)
        if text_chunk:
            yield text_chunk


def run_ollama_generate(prompt, model=OLLAMA_MODEL):
    response = generate(model=model, prompt=prompt)
    return extract_generate_text(response).strip()


def ensure_piper_ready():
    if not os.path.exists(PIPER_PATH):
        print(tr("Error: Piper binary not found. Check ~/piper_models/", "त्रुटि: Piper बाइनरी फेला परेन। ~/piper_models/ जाँच गर्नुहोस्"))
        return False
    if not os.path.exists(PIPER_MODEL):
        print(tr("Error: Piper model not found. Check ~/piper_models/", "त्रुटि: Piper मडेल फेला परेन। ~/piper_models/ जाँच गर्नुहोस्"))
        return False
    if not os.access(PIPER_PATH, os.X_OK):
        try:
            os.chmod(PIPER_PATH, os.stat(PIPER_PATH).st_mode | 0o111)
        except Exception as e:
            print(tr(f"Error: failed to set Piper executable: {e}", f"त्रुटि: Piper बाइनरी executable बनाउन सकिएन: {e}"))
            return False
        if not os.access(PIPER_PATH, os.X_OK):
            print(tr("Error: Piper binary is not executable.", "त्रुटि: Piper बाइनरी executable छैन।"))
            return False
    return True


def open_file_with_default_app(file_path):
    try:
        subprocess.Popen(
            ["xdg-open", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(tr(f"xdg-open error: {e}", f"xdg-open त्रुटि: {e}"))
        return False


def change_volume(step):
    try:
        result = subprocess.run(
            ["amixer", "-D", "pulse", "sset", "Master", step],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Volume command error: {e}")
        return False


def choose_terminal_action(query):
    return ai_pick_label(
        query=query,
        labels=["TERMINAL_SYSINFO", "TERMINAL_CLEAR_CACHE", "TERMINAL_NONE"],
        instructions_en=(
            "Choose the safest matching terminal action for this user request. "
            "Use TERMINAL_SYSINFO for system information checks, TERMINAL_CLEAR_CACHE for cache cleanup. "
            "If request does not clearly match one of these actions, return TERMINAL_NONE."
        ),
        instructions_ne=(
            "यो अनुरोधका लागि सुरक्षित टर्मिनल कार्य छान्नुहोस्। "
            "सिस्टम जानकारीका लागि TERMINAL_SYSINFO, क्यास सफाइका लागि TERMINAL_CLEAR_CACHE। "
            "स्पष्ट रूपमा नमिल्दा TERMINAL_NONE फर्काउनुहोस्।"
        ),
        default="TERMINAL_NONE",
    )


def run_safe_terminal_command(query):
    action = choose_terminal_action(query)
    if action == "TERMINAL_NONE":
        return (
            tr("No safe terminal command matched.", "सुरक्षित टर्मिनल आदेश पहिचान भएन।"),
            tr("Supported commands: system info, clear package cache.", "समर्थित आदेशहरू: system info, package cache सफा।"),
            False,
        )

    item = SAFE_TERMINAL_COMMANDS.get(action)
    if item is None:
        return (
            tr("Unsupported terminal action.", "असमर्थित टर्मिनल कार्य।"),
            "",
            False,
        )

    announce_text = item["announce"].get(CURRENT_LANGUAGE, item["announce"]["en"])
    try:
        result = subprocess.run(
            item["command"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        if not output:
            output = tr("Command finished successfully.", "आदेश सफलतापूर्वक सम्पन्न भयो।")
        return announce_text, output, result.returncode == 0
    except FileNotFoundError:
        return (
            announce_text,
            tr(
                f"`{item['command'][0]}` was not found. Please install the required package.",
                f"`{item['command'][0]}` भेटिएन। कृपया आवश्यक प्याकेज स्थापना गर्नुहोस्।",
            ),
            False,
        )
    except subprocess.TimeoutExpired:
        return announce_text, tr("Command timed out.", "आदेश धेरै ढिलो भयो (timeout)।"), False
    except Exception as e:
        return announce_text, tr(f"Command failed: {e}", f"आदेश चलाउँदा त्रुटि: {e}"), False


def call_ollama(prompt, system_prompt=None, stream=False, on_chunk=None):
    global chat_history
    if system_prompt is None:
        system_prompt = get_live_system_prompt()
    try:
        chat_history.append({'role': 'user', 'content': prompt})
        response_messages = [
            {'role': 'system', 'content': system_prompt},
            *chat_history,
        ]
        if stream:
            response_parts = []
            for piece in run_ollama_chat_stream(response_messages, model=OLLAMA_MODEL):
                response_parts.append(piece)
                if callable(on_chunk):
                    on_chunk(piece)
            assistant_response = "".join(response_parts).strip()
        else:
            assistant_response = run_ollama_chat(response_messages, model=OLLAMA_MODEL)
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        if len(chat_history) > MAX_CHAT_HISTORY:
            chat_history = chat_history[-MAX_CHAT_HISTORY:]
        return assistant_response
    except Exception as e:
        print(f"Ollama Error: {e}")
        return tr(
            "I cannot reach the local model right now. Please make sure Ollama is running.",
            "मेरो स्थानीय मडेलसँग जोडिन समस्या छ। कृपया सुनिश्चित गर्नुहोस् कि Ollama चलिरहेको छ।",
        )


def get_smart_intent(query):
    return ai_pick_label(
        query=query,
        labels=INTENT_LABELS,
        instructions_en=(
            "Classify the user request into the best action label. "
            "Use WEATHER_INFO ONLY if user explicitly asks about weather, temperature, or current conditions in a specific place. "
            "Use GENERAL_KNOWLEDGE for questions about places, countries, history, facts, information requests (like 'tell me about X', 'what is X', 'who is X'). "
            "Use CASUAL_CHAT for normal conversation, follow-up discussion, or uncertain requests. "
            "Use TERMINAL_COMMAND only if the user clearly wants a terminal operation."
        ),
        instructions_ne=(
            "प्रयोगकर्ताको अनुरोधलाई सबैभन्दा उपयुक्त action लेबलमा वर्गीकृत गर्नुहोस्। "
            "WEATHER_INFO मौसम, तापक्रम वा हालको अवस्थाबारे सोधेमा मात्र प्रयोग गर्नुहोस्। "
            "GENERAL_KNOWLEDGE ठाउँ, देश, इतिहास, तथ्य वा जानकारीको लागि प्रयोग गर्नुहोस् (जस्तै: 'X को बारेमा भन', 'X के हो', 'X को बारेमा केही भन')। "
            "सामान्य कुराकानी, follow-up वा अनिश्चित अवस्थामा CASUAL_CHAT छान्नुहोस्। "
            "टर्मिनल चलाउन स्पष्ट अनुरोध भए मात्र TERMINAL_COMMAND छान्नुहोस्।"
        ),
        default="CASUAL_CHAT",
    )

def estimate_tts_timeout(text):
    value = (text or "").strip()
    chars = max(1, len(value))
    words = max(1, len(value.split()))
    # Piper speed varies by voice/model/device; use a conservative estimate.
    estimated_seconds = max(chars / 5.5, words * 0.9) + 20
    return max(40, min(240, int(estimated_seconds)))


def stop_process_safe(process):
    if process is None or process.poll() is not None:
        return
    process.kill()
    try:
        process.wait(timeout=2)
    except Exception:
        pass


def pop_stream_speech_chunk(
    buffer_text,
    force=False,
    min_chars=90,
    target_chars=180,
    hard_max_chars=280,
):
    value = re.sub(r"\s+", " ", (buffer_text or "")).strip()
    if not value:
        return "", ""

    if not force and len(value) < min_chars:
        return "", value

    search_limit = min(len(value), hard_max_chars)
    window = value[:search_limit]

    cut_index = None

    # Prefer complete sentence boundaries for natural speech rhythm.
    for match in re.finditer(r"[.!?।](?:\s+|$)", window):
        if match.end() >= min_chars:
            cut_index = match.end()

    if cut_index is None:
        for match in re.finditer(r"[,;:](?:\s+|$)", window):
            if match.end() >= min_chars:
                cut_index = match.end()

    if cut_index is None:
        if not force and len(value) < hard_max_chars:
            return "", value
        preferred = min(search_limit, target_chars)
        left_space = window.rfind(" ", min_chars, preferred + 1)
        right_space = window.find(" ", preferred)
        if left_space > 0:
            cut_index = left_space
        elif right_space > 0:
            cut_index = right_space
        else:
            cut_index = search_limit

    chunk = value[:cut_index].strip()
    remainder = value[cut_index:].strip()
    return chunk, remainder


def normalize_stream_chunk_for_tts(text):
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    # Remove common markdown/control artifacts that sound awkward in TTS.
    normalized = normalized.replace("*", " ").replace("`", " ").replace("_", " ")
    normalized = re.sub(r"^#{1,6}\s*", "", normalized)
    normalized = re.sub(r"\b(assistant|user|system)\s*:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("…", ".")
    normalized = re.sub(r"\.{3,}", ".", normalized)
    # Repair token merges like "one.Basically" during streamed output.
    normalized = re.sub(r"([.!?।])([A-Za-z0-9\u0900-\u097F])", r"\1 \2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def response_asks_follow_up(text):
    normalized = normalize_stream_chunk_for_tts(text)
    if not normalized:
        return False
    return normalized.rstrip().endswith("?")


def tts_stream_worker(tts_queue):
    while True:
        chunk = tts_queue.get()
        if chunk is None:
            tts_queue.task_done()
            break
        try:
            cleaned_chunk = normalize_stream_chunk_for_tts(chunk)
            if cleaned_chunk:
                speak2(speech_safe_text(cleaned_chunk))
        finally:
            tts_queue.task_done()


def speak_streaming_piece(piece, stream_state, tts_queue=None):
    stream_state["buffer"] = stream_state.get("buffer", "") + (piece or "")
    while True:
        chunk, remainder = pop_stream_speech_chunk(stream_state.get("buffer", ""), force=False)
        if not chunk:
            stream_state["buffer"] = remainder
            break
        stream_state["buffer"] = remainder
        cleaned_chunk = normalize_stream_chunk_for_tts(chunk)
        if not cleaned_chunk:
            continue
        if tts_queue is None:
            speak2(speech_safe_text(cleaned_chunk))
        else:
            tts_queue.put(cleaned_chunk)
        stream_state["spoken_any"] = True


def flush_streaming_speech(stream_state, tts_queue=None):
    buffer_text = stream_state.get("buffer", "")
    chunk, remainder = pop_stream_speech_chunk(buffer_text, force=True)
    cleaned_chunk = normalize_stream_chunk_for_tts(chunk)
    cleaned_remainder = normalize_stream_chunk_for_tts(remainder)
    if cleaned_chunk:
        if tts_queue is None:
            speak2(speech_safe_text(cleaned_chunk))
        else:
            tts_queue.put(cleaned_chunk)
        stream_state["spoken_any"] = True
    elif cleaned_remainder:
        if tts_queue is None:
            speak2(speech_safe_text(cleaned_remainder))
        else:
            tts_queue.put(cleaned_remainder)
        stream_state["spoken_any"] = True
    stream_state["buffer"] = ""


def speak_logic(audio):
    safe_audio = speech_safe_text(audio, limit=220)
    if not safe_audio:
        return

    print(f"{tr('Assistant', 'सहायोगी')}: {safe_audio}")
    
    if not ensure_piper_ready():
        return

    piper_process = None
    aplay_process = None
    try:
        piper_process = subprocess.Popen(
            [PIPER_PATH, "--model", PIPER_MODEL, "--output_raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        aplay_process = subprocess.Popen(
            ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw"],
            stdin=piper_process.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if piper_process.stdin is not None:
            piper_process.stdin.write(safe_audio.encode("utf-8"))
            piper_process.stdin.close()
        if piper_process.stdout is not None:
            piper_process.stdout.close()
        timeout_sec = estimate_tts_timeout(safe_audio)
        piper_process.wait(timeout=timeout_sec)
        aplay_process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        stop_process_safe(piper_process)
        stop_process_safe(aplay_process)
        print(
            tr(
                "Speech timeout reached; skipping long utterance.",
                "धेरै लामो आवाजले timeout दियो, उक्त भाग स्किप गरियो।",
            )
        )
    except Exception as e:
        print(tr(f"Speech error: {e}", f"बोलीको त्रुटि: {e}"))

def speak(audio):
    ui.updateui("speaking")
    speak_logic(audio)
    ui.updateui("neutral")

def speak2(audio):
    speak_logic(audio)

def speak_wc(audio):
    ui.updateui("neutral")
    speak_logic(audio)

def speak_s(audio):
    ui.updateui("searching")
    speak_logic(audio)

def wishMe():
    hour = datetime.datetime.now().hour
    strTime = datetime.datetime.now().strftime("%I:%M %p")
    greeting = get_lang_config()["greeting"]

    if hour >= 0 and hour < 12:
        speak_wc(greeting["morning"])
    elif hour >= 12 and hour < 18:
        speak_wc(greeting["afternoon"])
    else:
        speak_wc(greeting["evening"])

    speak_wc(greeting["intro"].format(time=strTime))

def takeCommand(speak_feedback=True, timeout=5, phrase_time_limit=7):
    r = sr.Recognizer()
    lang_config = get_lang_config()

    ui.updateui("listening")
    with sr.Microphone() as source:
        print(lang_config["listen_text"])
        if speak_feedback:
            speak2(lang_config["listen_text"])
        
        r.adjust_for_ambient_noise(source, duration=0.3) 
        
        try:
            recordedaudio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            print(tr("Listening timed out.", "सुनिने समय समाप्त भयो।"))
            return "None"
            
    try:
        ui.updateui("recognizing")
        if speak_feedback:
            print(lang_config["recognizing_text"])
        query = r.recognize_google(recordedaudio, language=lang_config["speech_lang"])
        print(f"{tr('User said', 'प्रयोगकर्ता भन्नुभयो')}: {query}\n")
        return query
    except Exception as e:
        print(tr("Please say that again...", "पुनः भन्नुहोस् कृपया..."))
        return "None"

def open_website(url):
    webbrowser.open(url)

def handle_command(query, outputterminalBox):
    raw_query = (query or "").strip()
    query = raw_query.lower().strip()
    if query == "none" or not query:
        return

    language_switch_target = detect_language_switch_command(raw_query)
    if language_switch_target is not None:
        apply_language_change(language_switch_target, outputterminalBox=outputterminalBox)
        return

    intent = get_smart_intent(query)
    print(f"Smart Intent Detected: {intent}")

    if intent == "OPEN_YOUTUBE":
        speak(tr("Opening YouTube.", "YouTube खोली रहेको छ।"))
        open_website("https://www.youtube.com")

    elif intent == "OPEN_GOOGLE":
        speak(tr("Opening Google.", "Google खोली रहेको छ।"))
        open_website("https://www.google.com")

    elif intent == "OPEN_FACEBOOK":
        speak(tr("Opening Facebook.", "Facebook खोली रहेको छ।"))
        open_website("https://www.facebook.com")

    elif intent == "TELL_JOKE":
        joke = pyjokes.get_joke()
        outputterminalBox.appendPlainText(joke)
        speak(joke)

    elif intent == "GET_TIME":
        strTime = datetime.datetime.now().strftime("%I:%M %p")
        speak(tr(f"The time is {strTime}.", f"समय {strTime} छ।"))

    elif intent == "PLAY_SONG":
        speak(tr("What song should I play?", "मैले कुन गीत बजाऊँ?"))
        song = takeCommand()
        if song and song.lower() != "none":
            pywhatkit.playonyt(song)
            speak(tr(f"Playing {song}.", f"{song} बजाई रहेको छ।"))

    elif intent == "VOLUME_UP":
        speak(tr("Turning the volume up.", "आवाज बढाई रहेको छ।"))
        if not change_volume("5%+"):
            outputterminalBox.appendPlainText(
                tr("Could not increase volume (amixer).", "Volume बढाउन सकिएन (amixer)।")
            )

    elif intent == "VOLUME_DOWN":
        speak(tr("Turning the volume down.", "आवाज घटाई रहेको छ।"))
        if not change_volume("5%-"):
            outputterminalBox.appendPlainText(
                tr("Could not decrease volume (amixer).", "Volume घटाउन सकिएन (amixer)।")
            )

    elif intent == "WEATHER_INFO":
        search = 'temperature in kathmandu'
        url = f"https://www.google.com/search?q={search}"
        try:
            r = requests.get(url, timeout=8)
            data = BeautifulSoup(r.text, "html.parser")
            temp_tag = data.find("div", class_="BNeawe")
            temp = temp_tag.text if temp_tag else tr("weather data unavailable", "मौसम जानकारी उपलब्ध छैन")
            speak(tr(f"It is {temp} in Kathmandu.", f"काठमाडौंमा {temp} छ।"))
        except Exception as e:
            print(f"Weather error: {e}")
            speak(tr("I could not fetch weather right now.", "अहिले मौसम जानकारी ल्याउन सकिन।"))

    elif intent == "OPEN_VSCODE":
        speak(tr("Opening VS Code.", "VS Code खोली रहेको छ।"))
        subprocess.Popen(["code"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    elif intent == "SYSTEM_SHUTDOWN":
        speak(tr("Warning: do you really want to shut down?", "चेतावनी: के तपाई साँच्चै बन्द गर्न चाहनुहुन्छ?"))
        confirmation = takeCommand()
        if confirmation and confirmation.lower() != "none" and is_affirmative_response(confirmation):
            subprocess.run(["shutdown", "now"], check=False)

    elif intent == "TERMINAL_COMMAND":
        announce, command_output, command_success = run_safe_terminal_command(query)
        outputterminalBox.appendPlainText(command_output)
        if command_success:
            speak(announce)
        else:
            speak(f"{announce} {command_output}")

    elif intent == "CODE_HTML":
        speak(tr("Generating HTML code using Gemma 3.", "Gemma 3 को प्रयोग गरी HTML कोड उत्पादन गर्दैछु।"))
        prompt = get_lang_config()["code_html_prompt"].format(query=raw_query)
        
        try:
            code_text = run_ollama_generate(prompt, model=OLLAMA_MODEL)
            
            output_file = os.path.join(BASE_DIR, "code.html")
            with open(output_file, "w", encoding="utf-8") as file:
                cleaned_code_reply = clean_up_code(code_text)
                outputterminalBox.appendPlainText(tr("Code is written in code.html", "कोड Code.HTML मा लेखिएको छ।")) 
                speak(tr("Code is written in code.html", "कोड Code.HTML मा लेखिएको छ।"))
                file.write(cleaned_code_reply)
                open_file_with_default_app(output_file)
        except Exception as e:
            print(f"Ollama Error: {e}")
            speak(tr("Failed to generate HTML code locally.", "HTML कोड स्थानीय रूपमा उत्पादन गर्दा त्रुटि भयो।"))

    elif intent == "CODE_PYTHON":
        speak(tr("Generating Python code using Gemma 3.", "Gemma 3 को प्रयोग गरी Python कोड उत्पादन गर्दैछु।"))
        prompt = get_lang_config()["code_python_prompt"].format(query=raw_query)
            
        try:
            code_text = run_ollama_generate(prompt, model=OLLAMA_MODEL) 
                
            output_file = os.path.join(BASE_DIR, "code.py")
            with open(output_file, "w", encoding="utf-8") as file:
                cleaned_code_reply = clean_up_code(code_text)
                outputterminalBox.appendPlainText(tr("Code is written in code.py", "कोड Code.py मा लेखिएको छ।")) 
                speak(tr("Code is written in code.py", "कोड Code.py मा लेखिएको छ।"))
                file.write(cleaned_code_reply)
                open_file_with_default_app(output_file)
        except Exception as e:
            print(f"Ollama Error: {e}")
            speak(tr("Failed to generate Python code locally.", "Python कोड स्थानीय रूपमा उत्पादन गर्दा त्रुटि भयो।"))

    elif intent == "SELF_INFO":
        response = run_ollama_chat([
            {'role': 'system', 'content': get_lang_config()["self_info_prompt"]},
            {'role': 'user', 'content': raw_query}
        ], model=OLLAMA_MODEL)
        
        cleaned_reply = clean_up_text(response)
        outputterminalBox.appendPlainText(cleaned_reply)
        speak(speech_safe_text(cleaned_reply))

    elif intent == "GENERAL_KNOWLEDGE":
        # Handle general knowledge questions (about places, countries, history, facts)
        # Check if user is asking about a specific topic that needs live news
        detected_topic = None
        query_lower = raw_query.lower()
        
        # Detect specific topics in the query
        topic_keywords = {
            "f1": ["f1", "formula 1", "formula one", "grand prix", "moto gp", "racing"],
            "football": ["football", "soccer", "premier league", "champions league", "world cup"],
            "cricket": ["cricket", "ipl", "test match", "t20"],
            "tennis": ["tennis", "wimbledon", "us open", "french open"],
            "tech": ["tech", "technology", "apple", "google", "microsoft", "ai", "artificial intelligence"],
            "business": ["business", "stock market", "economy", "finance"],
            "science": ["science", "space", "nasa", "research"],
            "politics": ["politics", "election", "government", "parliament"],
            "health": ["health", "covid", "pandemic", "medical"],
            "nepal": ["nepal", "nepali", "kathmandu"],
            "india": ["india", "indian"],
            "usa": ["usa", "america", "american", "united states"],
            "uk": ["uk", "britain", "british", "england"],
        }
        
        for topic, keywords in topic_keywords.items():
            if any(kw in query_lower for kw in keywords):
                detected_topic = topic
                break
        
        # Fetch topic-specific news if detected
        topic_context = ""
        if detected_topic:
            topic_news = fetch_topic_news(detected_topic)
            if topic_news:
                topic_context = f"\n\nLatest {detected_topic.upper()} NEWS:\n{topic_news}"
                print(f"Fetched {detected_topic} news for query")
        
        # Build enhanced prompt with topic context
        base_system_prompt = get_live_system_prompt()
        if topic_context:
            # Prepend topic-specific news to the prompt
            enhanced_prompt = base_system_prompt.replace(
                "Use this context to answer accurately when users ask about current events or specific topics.",
                f"Use this context to answer accurately when users ask about current events or specific topics. {topic_context}"
            )
        else:
            enhanced_prompt = base_system_prompt
        
        speech_stream_state = {"buffer": "", "spoken_any": False}
        tts_queue = queue.Queue()
        tts_thread = threading.Thread(target=tts_stream_worker, args=(tts_queue,), daemon=True)
        tts_thread.start()

        def on_stream_chunk(piece):
            speak_streaming_piece(piece, speech_stream_state, tts_queue=tts_queue)

        cleaned_reply = clean_up_text(
            call_ollama(
                raw_query,
                system_prompt=enhanced_prompt,
                stream=True,
                on_chunk=on_stream_chunk,
            )
        )
        flush_streaming_speech(speech_stream_state, tts_queue=tts_queue)
        tts_queue.put(None)
        tts_queue.join()
        tts_thread.join(timeout=1)
        outputterminalBox.appendPlainText(cleaned_reply)

    else:
        # Handle casual chat and other queries with topic detection
        detected_topic = None
        query_lower = raw_query.lower()
        
        # Detect specific topics in the query
        topic_keywords = {
            "f1": ["f1", "formula 1", "formula one", "grand prix", "moto gp", "racing"],
            "football": ["football", "soccer", "premier league", "champions league", "world cup"],
            "cricket": ["cricket", "ipl", "test match", "t20"],
            "tennis": ["tennis", "wimbledon", "us open", "french open"],
            "tech": ["tech", "technology", "apple", "google", "microsoft", "ai", "artificial intelligence"],
            "business": ["business", "stock market", "economy", "finance"],
            "science": ["science", "space", "nasa", "research"],
            "politics": ["politics", "election", "government", "parliament"],
            "health": ["health", "covid", "pandemic", "medical"],
            "news": ["news", "latest news", "breaking news", "what's happening", "समाचार", "ताजा खबर", "आजको समाचार"],
            "nepal": ["nepal", "nepali", "kathmandu", "नेपाल", "काठमाडौं"],
            "india": ["india", "indian"],
            "usa": ["usa", "america", "american", "united states"],
            "uk": ["uk", "britain", "british", "england"],
        }
        
        for topic, keywords in topic_keywords.items():
            if any(kw in query_lower for kw in keywords):
                detected_topic = topic
                break
        
        # Fetch topic-specific news if detected
        topic_context = ""
        if detected_topic:
            # For general news queries, fetch fresh headlines
            if detected_topic == "news":
                # Refresh general news from RSS
                fetch_latest_news()
                topic_context = f"\n\nLatest Headlines:\n{current_news_context}"
            else:
                topic_news = fetch_topic_news(detected_topic)
                if topic_news:
                    topic_context = f"\n\nLatest {detected_topic.upper()} NEWS:\n{topic_news}"
            print(f"Fetched {detected_topic} news for query")
        
        # Build enhanced prompt with topic context
        base_system_prompt = get_live_system_prompt()
        if topic_context:
            enhanced_prompt = base_system_prompt.replace(
                "Here are the latest global news headlines:",
                topic_context
            )
        else:
            enhanced_prompt = base_system_prompt
        
        speech_stream_state = {"buffer": "", "spoken_any": False}
        tts_queue = queue.Queue()
        tts_thread = threading.Thread(target=tts_stream_worker, args=(tts_queue,), daemon=True)
        tts_thread.start()

        def on_stream_chunk(piece):
            speak_streaming_piece(piece, speech_stream_state, tts_queue=tts_queue)

        cleaned_reply = clean_up_text(
            call_ollama(
                raw_query,
                system_prompt=enhanced_prompt,
                stream=True,
                on_chunk=on_stream_chunk,
            )
        )
        flush_streaming_speech(speech_stream_state, tts_queue=tts_queue)
        tts_queue.put(None)
        tts_queue.join()
        tts_thread.join(timeout=1)
        outputterminalBox.appendPlainText(cleaned_reply)
        if not speech_stream_state.get("spoken_any", False):
            speak(speech_safe_text(cleaned_reply))

def minimize_to_tray():
    global tray_icon
    tray_icon = QSystemTrayIcon(QtGui.QIcon(asset_path("logo.png")), parent=app)
    tray_icon.show()
    Dialog.hide()

def restore_from_tray():
    global tray_icon
    tray_icon.hide()
    Dialog.show()

def process_command():
    query = takeCommand()
    if query and query.lower() != "none":
        ui.append_terminal_log(query, role="user")
    handle_command(query, ui.terminalOutputBox)


def process_conversation_session(max_turns=4, idle_timeout=12):
    turns = 0
    while turns < max_turns:
        query = takeCommand(speak_feedback=False, timeout=idle_timeout, phrase_time_limit=10)
        if not query or query.lower() == "none":
            if turns == 0:
                speak(tr("I am still here if you need me.", "म यही छु, चाहियो भने बोलाउनुहोस्।"))
            break

        if should_end_conversation(query):
            speak(tr("Alright, I will wait for your wake word.", "ठिक छ, म जागरण शब्दको प्रतीक्षा गर्छु।"))
            break

        ui.append_terminal_log(query, role="user")
        handle_command(query, ui.terminalOutputBox)
        turns += 1

        if turns < max_turns:
            speak2(tr("Anything else?", "अरु केही?"))

def clean_up_text(texts):
    if texts is None:
        return ""
    cleaned_text = str(texts).replace("```", " ").replace("*", " ").strip()
    cleaned_text = " ".join(cleaned_text.split())
    return cleaned_text

def speech_safe_text(text, limit=220):
    value = (text or "").strip()
    if len(value) <= limit:
        return value

    # Prefer complete sentences to reduce awkward cut-offs during TTS.
    sentence_parts = re.split(r"(?<=[.!?])\s+", value)
    chosen = []
    current_len = 0
    for part in sentence_parts:
        part = part.strip()
        if not part:
            continue
        projected = current_len + len(part) + (1 if chosen else 0)
        if projected > limit:
            break
        chosen.append(part)
        current_len = projected

    if chosen:
        joined = " ".join(chosen).strip()
        return joined if len(joined) == len(value) else f"{joined}..."

    truncated = value[:limit].rsplit(" ", 1)[0].strip()
    return (truncated or value[:limit]).strip() + "..."

def clean_up_code(texts):
    if texts is None:
        return ""
    cleaned_text = str(texts).strip()
    if cleaned_text.startswith("```"):
        code_lines = cleaned_text.splitlines()
        if code_lines and code_lines[0].startswith("```"):
            code_lines = code_lines[1:]
        if code_lines and code_lines[-1].strip() == "```":
            code_lines = code_lines[:-1]
        cleaned_text = "\n".join(code_lines).strip()
    return cleaned_text

def clean_up_url(texts):
    cleaned_url = texts.replace('dot', '.').replace('slash', '/')
    return cleaned_url

class ListenThread(QtCore.QThread):
    def run(self):
        ui.updateui("neutral")
        
        while True:
            try:
                lang_config = get_lang_config()
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.8)
                    print(tr("Listening for wake words...", "जागरण शब्दका लागि सुनिरहेको छु..."))
                    
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=3)
                
                text = recognizer.recognize_google(audio, language=lang_config["speech_lang"]).lower()
                print(f"{tr('Heard', 'सुने')}: {text}")
                
                if should_wake_from_utterance(text):
                    print(tr("Wake word detected.", "जागरण आदेश पत्ता लगाइयो!"))
                    speak(lang_config["wake_ack"])
                    process_conversation_session()
                    
            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                err = tr(
                    f"Could not request results from Google Speech Recognition service; {e}",
                    f"Google Speech Recognition सेवासँग परिणाम अनुरोध गर्न सकिएन; {e}",
                )
                print(err)
                ui.terminalOutputBox.appendPlainText(err) # type: ignore
            except Exception as e:
                print(tr(f"ListenThread error: {e}", f"ListenThread मा त्रुटि: {e}"))
                time.sleep(1)

def on_finished():
    global tray_icon
    if tray_icon is not None:
        tray_icon.hide()
        del tray_icon
    sys.exit()

class NewsRefreshThread(QtCore.QThread):
    """Background thread to refresh news every 3 hours."""
    def run(self):
        # Initial fetch at startup
        fetch_latest_news()
        
        # Refresh every 3 hours (3 * 60 * 60 * 1000 milliseconds)
        refresh_interval = 3 * 60 * 60 * 1000  # 3 hours in ms
        
        while True:
            QtCore.QThread.sleep(refresh_interval // 1000)
            fetch_latest_news()


if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    Dialog = QtWidgets.QDialog()
    ui = Ui_Dialog()
    ui.setupUi(Dialog)
    ui.updateui("neutral")
    ui.append_terminal_log("System initialized successfully.", role="system")
    ui.append_terminal_log(f"Active language: {LANGUAGE_SETTINGS[CURRENT_LANGUAGE]['label']}", role="system")
    ui.append_terminal_log(f"Voice model: {os.path.basename(PIPER_MODEL)}", role="system")

    Dialog.show()
    Dialog.finished.connect(on_finished)

    wishMe()

    # Start background news refresh thread (runs at startup and every 3 hours)
    news_thread = NewsRefreshThread()
    news_thread.start()
    
    listen_thread = ListenThread()
    listen_thread.start()

    sys.exit(app.exec_())
