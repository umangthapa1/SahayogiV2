# S.A.H.A.Y.O.G.I - Desktop Voice Assistant

A multilingual desktop AI voice assistant powered by [Ollama](https://ollama.ai/) (Gemma 3) with support for English and Nepali languages. Built with PyQt5 for a modern web-based UI, it provides hands-free voice interaction for various tasks.

## Features

### Core Capabilities
- 🎙️ **Voice Recognition** - Hands-free voice command recognition using Google Speech Recognition
- 🤖 **AI-Powered Chat** - Powered by Ollama (Gemma 3 4B model) for natural conversations
- 🔊 **Text-to-Speech** - Uses Piper for high-quality voice output in both English and Nepali
- 🌐 **Bilingual Support** - Seamlessly switch between English (en) and Nepali (ne)

### Functional Commands
| Command Type | Examples |
|-------------|----------|
| **Web Browsing** | "Open YouTube", "Open Google", "Open Facebook" |
| **Music** | "Play [song name]" - Plays on YouTube |
| **Volume** | "Volume up", "Volume down" |
| **Information** | "What's the time?", "Tell me about [topic]" |
| **Weather** | "What's the weather?" (Kathmandu) |
| **Code Generation** | "Generate HTML for [description]", "Write Python code for [description]" |
| **Entertainment** | "Tell me a joke" |
| **System** | "Open VS Code", "System shutdown" |
| **News** | "What's the latest news?", "Tell me about [F1/tech/cricket/etc.]" |

### Advanced Features
- **Live News Integration** - Fetches latest headlines from Google News RSS feeds
- **Topic-Specific News** - Specialized news for F1, football, cricket, tech, business, science, politics, health, Nepal, India, USA, UK
- **Streaming Speech** - Real-time text-to-speech while AI generates responses
- **System Tray** - Minimize to system tray for background operation
- **Smart Intent Detection** - AI-powered command classification
- **Multi-turn Conversations** - Context-aware dialogue sessions

## Requirements

### System Dependencies
```bash
# Core dependencies
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    libasound2-dev \
    portaudio19-dev \
    ffmpeg

# Piper TTS (text-to-speech)
# Download from: https://github.com/rhasspy/piper
```

### Python Dependencies
```bash
pip install \
    PyQt5 \
    PyQtWebEngine \
    speech_recognition \
    requests \
    beautifulsoup4 \
    pywhatkit \
    pyjokes \
    ollama
```

### Required Assets
```
~/piper_models/
├── voice.onnx              # Default English voice
├── en_US-amy-medium.onnx   # English voice model
└── ne_NP-chitwan-medium.onnx  # Nepali voice model
```

## Installation

1. **Clone the repository**
   ```bash
   cd /home/httpsumang/Downloads/sahayogi-main
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Setup Piper TTS**
   ```bash
   # Download Piper binary and voices
   cd ~
   mkdir -p piper_models
   cd piper_models
   
   # Download Piper executable
   wget https://github.com/rhasspy/piper/releases/download/2024.11.14/piper-linux-x64.tar.gz
   tar -xzf piper-linux-x64.tar.gz
   
   # Download voice models
   # English voice
   wget https://github.com/rhasspy/piper-voices/raw/main/v1/en_US/amy/medium/en_US-amy-medium.onnx
   wget https://github.com/rhasspy/piper-voices/raw/main/v1/en_US/amy/medium/en_US-amy-medium.onnx.json
   
   # Nepali voice
   wget https://github.com/rhasspy/piper-voices/raw/main/v1/ne_NP/chitwan/medium/ne_NP-chitwan-medium.onnx
   wget https://github.com/rhasspy/piper-voices/raw/main/v1/ne_NP/chitwan/medium/ne_NP-chitwan-medium.onnx.json
   ```

4. **Start Ollama**
   ```bash
   # Ensure Ollama is running with Gemma 3
   ollama serve
   ollama pull gemma3:4b
   ```

5. **Run the application**
   ```bash
   cd linux/Better_UI
   python3 V1.py
   ```

## Usage

### Voice Commands
The assistant listens for a wake word to begin a conversation session. Once activated, you can issue multiple commands in a session.

**Starting a Session:**
- Simply speak naturally - the assistant detects when you're addressing it
- Say commands like "Hello", "Hey", or any wake phrase

**Sample Commands:**
```
User: Open YouTube
Assistant: Opening YouTube.

User: What time is it?
Assistant: The time is 03:45 PM.

User: Tell me about Nepal
Assistant: [Provides information about Nepal with latest news context]

User: Generate HTML for a login form
Assistant: [Generates HTML code and opens it in browser]

User: Switch to Nepali / अब नेपालीमा बोलौं
Assistant: Language switched to Nepali. / भाषा नेपालीमा परिवर्तन भयो।
```

### Text Input
You can also type commands in the UI's terminal input field instead of using voice.

## Project Structure

```
linux/Better_UI/
├── V1.py              # Main application (this file)
├── ui.html            # Web-based user interface
├── assets/            # Application assets (logo, icons)
├── news_cache.txt     # Cached news data
└── code.html          # Generated code output
```

## Architecture

### UI Layer (PyQt5 + Web)
- **PyQt5** - Desktop application framework
- **QWebEngineView** - Embedded Chromium browser for modern UI
- **QWebChannel** - JavaScript-Python bridge for bidirectional communication

### Voice Processing
- **speech_recognition** - Google Speech Recognition API for STT
- **Piper** - Neural text-to-speech for natural-sounding voices

### AI Layer
- **Ollama** - Local LLM inference engine
- **Gemma 3:4b** - Primary AI model
- **Smart Intent Detection** - AI-powered command classification
- **Live System Prompt** - Context-aware prompts with news data

### News System
- **Google News RSS** - Source for live headlines
- **Topic Detection** - Automatic topic extraction from queries
- **Caching** - Local cache with 3-hour refresh interval

## Configuration

### Language Settings
The application supports two languages configured in `LANGUAGE_SETTINGS`:

```python
LANGUAGE_SETTINGS = {
    "en": {
        "label": "English",
        "speech_lang": "en-US",
        "piper_models": ["~/piper_models/en_US-amy-medium.onnx", ...]
    },
    "ne": {
        "label": "नेपाली",
        "speech_lang": "ne-NP", 
        "piper_models": ["~/piper_models/ne_NP-chitwan-medium.onnx", ...]
    }
}
```

### Ollama Configuration
```python
OLLAMA_MODEL = "gemma3:4b"  # Default model
MAX_CHAT_HISTORY = 24       # Conversation history limit
```

## Troubleshooting

### Common Issues

**"Ollama is not running"**
```bash
# Start Ollama service
ollama serve

# Pull the model if needed
ollama pull gemma3:4b
```

**"Piper not found"**
```bash
# Verify Piper installation
ls ~/piper_models/piper
ls ~/piper_models/voice.onnx

# Make executable
chmod +x ~/piper_models/piper
```

**"Speech recognition not working"**
- Check microphone permissions
- Verify internet connection (Google Speech API requires internet)
- Adjust `recognizer.energy_threshold` if needed

**"News not loading"**
- Check internet connection
- Verify RSS feed accessibility
- Review `news_cache.txt` for cached data

## License

This project is developed for educational and personal use.

## Acknowledgments

- [Ollama](https://ollama.ai/) - For local LLM inference
- [Piper](https://github.com/rhasspy/piper) - For neural text-to-speech
- [Google Speech Recognition](https://cloud.google.com/speech-to-text) - For voice recognition
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) - For desktop UI framework
