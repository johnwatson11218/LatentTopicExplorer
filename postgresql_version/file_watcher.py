#!/usr/bin/env python3
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

WATCH_DIR = Path("/home/pi/watched")  # change to your folder

def handle_new_file(filepath: Path) -> None:
    # This is your “invoke a function” hook
    print(f"[NEW FILE] {filepath.name}")
    # You can also use the full path:
    # print(f"[NEW FILE FULL PATH] {filepath}")

class NewFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        # Ignore directory creation events
        if event.is_directory:
            return
        path = Path(event.src_path)
        handle_new_file(path)

def main() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)

    event_handler = NewFileHandler()
    observer = Observer()
    observer.schedule(event_handler, str(WATCH_DIR), recursive=False)

    observer.start()
    print(f"Watching {WATCH_DIR} for new files...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()