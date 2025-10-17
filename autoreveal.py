#!/usr/bin/env python3
import argparse
import html
import os
import re
import http.server
import socketserver
import threading
import time
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup


extension_to_lang = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".css": "css",
    ".html": "html",
    ".xml": "xml",
    ".json": "json",
    ".md": "markdown",
    ".sh": "bash",
    ".sql": "sql",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".mermaid": "mermaid",
    # Add more as needed
}

# Simple approach: use polling instead of WebSocket
reload_flag = threading.Event()


def inject_live_reload_script(html_content):
    """Inject live reload polling script into HTML"""
    live_reload_script = """
    <script>
    (function() {
        let lastReloadTime = Date.now();
        
        function checkForReload() {
            fetch('/reload-check?' + lastReloadTime)
                .then(response => response.json())
                .then(data => {
                    if (data.reload) {
                        window.location.reload();
                    }
                })
                .catch(() => {
                    // Server might be restarting, try again
                })
                .finally(() => {
                    setTimeout(checkForReload, 1000);
                });
        }
        
        // Start checking after page loads
        setTimeout(checkForReload, 1000);
    })();
    </script>
    """

    # Insert before closing </body> tag
    return html_content.replace("</body>", f"{live_reload_script}</body>")


def notify_reload():
    """Signal that a reload should happen"""
    reload_flag.set()
    print("Reload signal sent")


class ReloadHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/reload-check"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            should_reload = reload_flag.is_set()
            if should_reload:
                reload_flag.clear()

            response = json.dumps({"reload": should_reload})
            self.wfile.write(response.encode())
        else:
            super().do_GET()


def process_loads(soup, base_dir):
    changed = False
    for elem in soup.find_all(attrs={"data-load": True}):
        load_path = elem.get("data-load")
        if load_path:
            full_path = os.path.join(base_dir, load_path)
            if os.path.exists(full_path):
                with open(full_path, "r") as f:
                    loaded_content = f.read()

                ext = os.path.splitext(load_path)[1].lower()

                if ext == ".html":
                    # Replace ./ in loaded_content
                    loaded_content = re.sub(
                        r'src="\./([^"]*)"',
                        lambda m: f'src="{os.path.dirname(load_path)}/{m.group(1)}"',
                        loaded_content,
                    )
                    loaded_content = re.sub(
                        r'data-load="\./([^"]*)"',
                        lambda m: f'data-load="{os.path.dirname(load_path)}/{m.group(1)}"',
                        loaded_content,
                    )

                    # Parse loaded
                    loaded_soup = BeautifulSoup(loaded_content, "html.parser")

                    # Replace elem contents
                    elem.clear()
                    if loaded_soup.body:
                        elem.extend(loaded_soup.body.contents)
                    else:
                        elem.extend(loaded_soup.contents)
                    del elem["data-load"]
                    changed = True
                elif ext == ".mermaid":
                    # Special handling for mermaid diagrams
                    # Create structure:
                    # <span class="diagram-data" style="display: none">CONTENT</span>
                    # <div class="diagram-display"></div>
                    diagram_html = f"""<span class="diagram-data" style="display: none">{loaded_content}</span><div class="diagram-display"></div>"""
                    diagram_soup = BeautifulSoup(diagram_html, "html.parser")

                    elem.clear()
                    elem.extend(diagram_soup.contents)
                    del elem["data-load"]
                    changed = True
                else:
                    # For code files, wrap in <pre><code>
                    lang = extension_to_lang.get(ext, ext[1:] if ext else "text")
                    code_content = f'<pre><code class="language-{lang}">{html.escape(loaded_content)}</code></pre>'
                    code_soup = BeautifulSoup(code_content, "html.parser")

                    # Propagate data-* attributes from source elem to the <code> element
                    code_elem = code_soup.find("code")
                    if code_elem:
                        for attr_name, attr_value in elem.attrs.items():
                            # Copy all data-* attributes except data-load
                            if (
                                attr_name.startswith("data-")
                                and attr_name != "data-load"
                            ):
                                code_elem[attr_name] = attr_value

                    elem.clear()
                    elem.extend(code_soup.contents)
    if changed:
        process_loads(soup, base_dir)


def build_slides(
    base_path, slides_dir, base_html_path, output_html_path, enable_live_reload=False
):
    # Read base.html
    with open(base_html_path, "r") as f:
        base_content = f.read()

    # Get subfolders in slides, sorted alphabetically
    subfolders = sorted(
        [
            d
            for d in os.listdir(slides_dir)
            if os.path.isdir(os.path.join(slides_dir, d))
        ]
    )

    slides_content = ""
    for folder in subfolders:
        index_path = os.path.join(slides_dir, folder, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r") as f:
                slide_content = f.read()

            # Replace ./ in src="..." and data-load="..."
            slide_content = re.sub(
                r'src="\./([^"]*)"', rf'src="slides/{folder}/\1"', slide_content
            )
            slide_content = re.sub(
                r'data-load="\./([^"]*)"',
                rf'data-load="slides/{folder}/\1"',
                slide_content,
            )

            # Process data-load attributes recursively
            soup = BeautifulSoup(slide_content, "html.parser")
            process_loads(soup, base_path)
            slide_content = str(soup)

            slides_content += slide_content
        else:
            print(f"Warning: index.html not found in {folder}")

    # Insert into .slides div
    # Find <div class="slides"></div> and replace with <div class="slides">slides_content</div>
    # Use lambda to avoid issues with backslashes in LaTeX/math equations
    base_content = re.sub(
        r'(<div class="slides">)\s*</div>',
        lambda m: f"{m.group(1)}\n{slides_content}\n</div>",
        base_content,
        flags=re.DOTALL,
    )

    # Inject live reload script if enabled
    if enable_live_reload:
        base_content = inject_live_reload_script(base_content)

    # Write the new index.html
    with open(output_html_path, "w") as f:
        f.write(base_content)

    print(f"Built index.html with slides from {len(subfolders)} folders.")


def watch_files(
    base_path,
    slides_dir,
    base_html_path,
    output_html_path,
    watch,
    enable_live_reload=False,
):
    if not watch:
        return

    # Initial collect files to watch
    files_to_watch = [base_html_path]
    subfolders = sorted(
        [
            d
            for d in os.listdir(slides_dir)
            if os.path.isdir(os.path.join(slides_dir, d))
        ]
    )
    for folder in subfolders:
        index_path = os.path.join(slides_dir, folder, "index.html")
        if os.path.exists(index_path):
            files_to_watch.append(index_path)

    # Get initial mtimes
    mtimes = {f: os.path.getmtime(f) for f in files_to_watch if os.path.exists(f)}

    while True:
        time.sleep(1)
        # Check for new/removed subfolders
        current_subfolders = sorted(
            [
                d
                for d in os.listdir(slides_dir)
                if os.path.isdir(os.path.join(slides_dir, d))
            ]
        )
        if current_subfolders != subfolders:
            print("Slides folder structure changed, updating watch list...")
            subfolders = current_subfolders
            files_to_watch = [base_html_path]
            for folder in subfolders:
                index_path = os.path.join(slides_dir, folder, "index.html")
                if os.path.exists(index_path):
                    files_to_watch.append(index_path)
            # Update mtimes for new files
            mtimes = {
                f: os.path.getmtime(f) for f in files_to_watch if os.path.exists(f)
            }
            # Rebuild since structure changed
            build_slides(
                base_path,
                slides_dir,
                base_html_path,
                output_html_path,
                enable_live_reload,
            )
            if enable_live_reload:
                notify_reload()
            continue

        changed = False
        for f in files_to_watch:
            if os.path.exists(f) and os.path.getmtime(f) != mtimes.get(f, 0):
                changed = True
                mtimes[f] = os.path.getmtime(f)
        if changed:
            print("File change detected, rebuilding...")
            build_slides(
                base_path,
                slides_dir,
                base_html_path,
                output_html_path,
                enable_live_reload,
            )
            if enable_live_reload:
                notify_reload()


def main():
    parser = argparse.ArgumentParser(description="Build and serve reveal.js slides.")
    parser.add_argument(
        "--port", type=int, default=8085, help="Port to serve on (default: 8085)"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for file changes and rebuild automatically",
    )
    parser.add_argument(
        "--live-reload",
        action="store_true",
        help="Enable live reload in browser when files change",
    )
    args = parser.parse_args()

    base_path = os.path.dirname(os.path.abspath(__file__))
    slides_dir = os.path.join(base_path, "slides")
    base_html_path = os.path.join(base_path, "base.html")
    output_html_path = os.path.join(base_path, "index.html")

    # Initial build
    build_slides(
        base_path, slides_dir, base_html_path, output_html_path, args.live_reload
    )

    # Start watcher if --watch
    if args.watch:
        watcher_thread = threading.Thread(
            target=watch_files,
            args=(
                base_path,
                slides_dir,
                base_html_path,
                output_html_path,
                args.watch,
                args.live_reload,
            ),
        )
        watcher_thread.daemon = True
        watcher_thread.start()
        print("Watching for file changes...")

    # Serve on the port
    os.chdir(base_path)
    handler = (
        ReloadHTTPRequestHandler
        if args.live_reload
        else http.server.SimpleHTTPRequestHandler
    )

    # Allow socket reuse to prevent "Address already in use" errors
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(
            f"Serving on port {args.port}. Open http://localhost:{args.port}/index.html"
        )
        if args.live_reload:
            print("Live reload enabled - browser will auto-refresh on file changes")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
