# Google Colab + Ollama Setup Guide

Since running large AI models requires a lot of GPU power, we use Google Colab to get a **free T4 GPU**. We then use **Ngrok** to create a secure tunnel so your local backend (`app.py`) can communicate with the Colab GPU.

Here is the exact code you need to run on Colab to get both of our models running.

---

### Step 1: Create the Colab Notebook

1. Go to [Google Colab](https://colab.research.google.com/) and click **New Notebook**.
2. In the top menu, go to **Runtime > Change runtime type**.
3. Under *Hardware accelerator*, select **T4 GPU** and click Save.

---

### Step 2: Get your Ngrok Token

1. Go to [ngrok.com](https://dashboard.ngrok.com/signup) and log in or create a free account.
2. On the left menu, click **Your Authtoken**.
3. Copy your token and replace `YOUR_NGROK_TOKEN_HERE` in the script below.

---

### Step 3: Run the Server Script

Copy the Python code below and paste it into the first code cell in your Colab notebook.

```python
# 1. Install Ollama & Dependencies (Output completely muted to hide warnings)
!apt-get update -qq && apt-get install -y -qq zstd pciutils > /dev/null 2>&1
!curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1
!pip install pyngrok -q > /dev/null 2>&1

import os
import threading
import time
from pyngrok import ngrok

# 2. Start the Ollama Server in the Background
def start_ollama():
    os.system("OLLAMA_HOST=0.0.0.0 ollama serve")

threading.Thread(target=start_ollama, daemon=True).start()
print("Starting Ollama server...\n")
time.sleep(3) # Wait for the server to boot

# 3. Pull BOTH of our models (Embedding + Generation)
print("Pulling nomic-embed-text (for fast vector search)...")
!ollama pull nomic-embed-text > /dev/null 2>&1

print("Pulling gemma4:12b-it-qat (for smart answer generation)...")
!ollama pull gemma4:12b-it-qat > /dev/null 2>&1

# 4. Open the Ngrok Tunnel
# --- REPLACE THIS STRING WITH YOUR NGROK TOKEN ---
ngrok.set_auth_token("YOUR_NGROK_TOKEN_HERE")
public_url = ngrok.connect(11434).public_url

print("\n" + "="*60)
print(f"✅ OLLAMA CLOUD URL: {public_url}")
print("="*60 + "\n")
print("Copy the URL above and paste it into your local .env file:")
print(f'OLLAMA_BASE_URL="{public_url}"')
```

Click the **Play** button next to the cell. It will take 3-5 minutes to download the large `gemma4` model. Once it finishes, it will print out a green checkmark and an `ngrok-free.app` URL.

---

### Step 4: Connect Your Local Backend

1. Open your local `.env` file in VS Code.
2. Replace the `OLLAMA_BASE_URL` with the new URL printed by Colab.

```env
OLLAMA_BASE_URL=https://<your-new-url>.ngrok-free.app
OLLAMA_MODEL=gemma4:12b-it-qat
OLLAMA_EMBED_MODEL=nomic-embed-text
```

**Note:** Colab will disconnect if you leave it completely idle for too long, or after about 12 hours. When that happens, just go back to Colab, restart the runtime, run the cell again, and put the *new* Ngrok URL into your `.env` file!
