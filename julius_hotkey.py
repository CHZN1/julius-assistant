"""Global stop hotkey: always works, whatever the microphone, speakers or echo do. It stops generation and playback at once.

macOS asks once to let the terminal (or Python) monitor input; without that permission the listener cannot start and Julius says so."""
def start(stop, combo="<ctrl>+<alt>+s", say=print, factory=None):
    """Register `combo` globally; `stop()` is called on a worker thread. Returns the listener, or None when it cannot start."""
    try:
        if factory is None:
            from pynput import keyboard
            factory = keyboard.GlobalHotKeys
        listener = factory({combo: stop}); listener.start()
        if getattr(listener, "IS_TRUSTED", True) is False:  # macOS lets the listener start but delivers no keys until Accessibility is granted
            listener.stop(); raise PermissionError("allow this Python under System Settings > Privacy & Security > Accessibility")
        say(f"  [hotkey {combo} active]"); return listener
    except Exception as e:
        say(f"  [hotkey {combo} unavailable: {e}]"); return None
