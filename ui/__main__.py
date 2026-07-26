"""Launch the simulator UI:  python -m ui  [--port 8000] [--no-browser]"""

from __future__ import annotations

import argparse
import threading
import webbrowser

from ui.server import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulator UI server.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-browser", action="store_true", help="don't open a browser tab"
    )
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Simulator UI on {url}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
