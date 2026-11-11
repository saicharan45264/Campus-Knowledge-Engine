"""
app.py — Compatibility entry point forwarding to main.py
"""
import uvicorn
from main import app

if __name__ == "__main__":
    import webbrowser
    from threading import Timer

    def open_browser():
        webbrowser.open("http://localhost:8000")

    Timer(1.5, open_browser).start()
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)

