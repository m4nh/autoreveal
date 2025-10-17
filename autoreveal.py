#!/usr/bin/env python3
"""
AutoReveal - Automatic Reveal.js Slide Builder and Server

This module provides functionality to build and serve Reveal.js presentations
by automatically processing slide folders, handling external file loading,
and optionally providing live reload capabilities for development.
"""

import argparse
import html
import http.server
import json
import os
import re
import socketserver
import threading
import time
from typing import Dict, List, Optional

from bs4 import BeautifulSoup


# Mapping of file extensions to their corresponding language identifiers
# Used for syntax highlighting in code blocks
extension_to_lang: Dict[str, str] = {
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

# Threading event flag for coordinating browser reload signals
# Uses polling approach instead of WebSocket for simplicity
reload_flag: threading.Event = threading.Event()


def inject_live_reload_script(html_content: str) -> str:
    """
    Inject a live reload polling script into the HTML content.

    This function adds a JavaScript snippet that periodically polls the server
    to check if a reload is needed. When a reload signal is detected, the page
    automatically refreshes.

    Args:
        html_content: The HTML content to inject the script into.

    Returns:
        The modified HTML content with the live reload script injected before
        the closing </body> tag.
    """
    # JavaScript that polls the server every second to check for reload signals
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
                    // Server might be restarting, silently ignore and retry
                })
                .finally(() => {
                    // Poll again after 1 second
                    setTimeout(checkForReload, 1000);
                });
        }
        
        // Start polling after page loads
        setTimeout(checkForReload, 1000);
    })();
    </script>
    """

    # Insert the script before the closing </body> tag
    return html_content.replace("</body>", f"{live_reload_script}</body>")


def notify_reload() -> None:
    """
    Signal that a reload should happen in all connected browsers.

    Sets the global reload flag which will be picked up by the next
    polling request from the browser's live reload script.
    """
    reload_flag.set()
    print("Reload signal sent")


class ReloadHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """
    Custom HTTP request handler that supports live reload functionality.

    Extends SimpleHTTPRequestHandler to add a special endpoint for checking
    reload status. When browsers poll the /reload-check endpoint, this handler
    responds with the current reload status.
    """

    def do_GET(self) -> None:
        """
        Handle GET requests, including the special /reload-check endpoint.

        If the request is for /reload-check, respond with JSON indicating
        whether a reload should occur. Otherwise, delegate to the parent
        class's file serving functionality.
        """
        if self.path.startswith("/reload-check"):
            # Respond to the reload check with JSON
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # Check if reload flag is set, then clear it
            should_reload = reload_flag.is_set()
            if should_reload:
                reload_flag.clear()

            # Send JSON response indicating whether reload is needed
            response = json.dumps({"reload": should_reload})
            self.wfile.write(response.encode())
        else:
            # Delegate to parent class for normal file serving
            super().do_GET()


def process_loads(soup: BeautifulSoup, base_dir: str) -> None:
    """
    Recursively process all elements with data-load or data-load-code attributes.

    This function finds all elements with data-load or data-load-code attributes
    and loads content from the specified file:

    data-load behavior (smart loading):
    - .html files: Parse and include content, adjusting relative paths
    - .mermaid files: Wrap in diagram containers for Mermaid rendering
    - Other files: Wrap in <pre><code> with syntax highlighting

    data-load-code behavior (always show as code):
    - ALL files (including .html and .mermaid): Wrap in <pre><code> with syntax highlighting
    - Use this when you want to display the source code of HTML/Mermaid files

    The function is called recursively to handle nested data-load attributes.

    Args:
        soup: BeautifulSoup object containing the parsed HTML to process.
        base_dir: Base directory path for resolving relative file paths.
    """
    changed = False

    # Find all elements with data-load or data-load-code attribute
    for elem in soup.find_all(attrs={"data-load": True}):
        load_path = elem.get("data-load")
        is_code_load = False  # This is a regular data-load

        if load_path:
            full_path = os.path.join(base_dir, load_path)
            if os.path.exists(full_path):
                # Read the content from the external file
                with open(full_path, "r") as f:
                    loaded_content = f.read()

                ext = os.path.splitext(load_path)[1].lower()

                # For regular data-load, use smart loading based on file type
                if ext == ".html":
                    # For HTML files, adjust relative paths to maintain correct references
                    # Replace ./ in src attributes with proper relative path
                    loaded_content = re.sub(
                        r'src="\./([^"]*)"',
                        lambda m: f'src="{os.path.dirname(load_path)}/{m.group(1)}"',
                        loaded_content,
                    )
                    # Replace ./ in data-load attributes with proper relative path
                    loaded_content = re.sub(
                        r'data-load="\./([^"]*)"',
                        lambda m: f'data-load="{os.path.dirname(load_path)}/{m.group(1)}"',
                        loaded_content,
                    )
                    # Replace ./ in data-load-code attributes with proper relative path
                    loaded_content = re.sub(
                        r'data-load-code="\./([^"]*)"',
                        lambda m: f'data-load-code="{os.path.dirname(load_path)}/{m.group(1)}"',
                        loaded_content,
                    )

                    # Parse the loaded HTML content
                    loaded_soup = BeautifulSoup(loaded_content, "html.parser")

                    # Replace the element's contents with loaded content
                    elem.clear()
                    if loaded_soup.body:
                        # If there's a body tag, use only its contents
                        elem.extend(loaded_soup.body.contents)
                    else:
                        # Otherwise use all contents
                        elem.extend(loaded_soup.contents)
                    del elem["data-load"]
                    changed = True

                elif ext == ".mermaid":
                    # For Mermaid diagrams, create special container structure
                    # Hidden span contains the diagram source, div is the render target
                    diagram_html = f"""<span class="diagram-data" style="display: none">{loaded_content}</span><div class="diagram-display"></div>"""
                    diagram_soup = BeautifulSoup(diagram_html, "html.parser")

                    elem.clear()
                    elem.extend(diagram_soup.contents)
                    del elem["data-load"]
                    changed = True

                else:
                    pass

    # Process data-load-code attributes (always treat as code)
    for elem in soup.find_all(attrs={"data-load-code": True}):
        load_path = elem.get("data-load-code")

        if load_path:
            full_path = os.path.join(base_dir, load_path)
            if os.path.exists(full_path):
                # Read the content from the external file
                with open(full_path, "r") as f:
                    loaded_content = f.read()

                ext = os.path.splitext(load_path)[1].lower()

                # Always treat as code - wrap in appropriate syntax highlighting tags
                lang = extension_to_lang.get(ext, ext[1:] if ext else "text")
                code_content = f'<pre><code class="language-{lang}">{html.escape(loaded_content)}</code></pre>'
                code_soup = BeautifulSoup(code_content, "html.parser")

                # Propagate data-* attributes from source element to the code element
                # This preserves attributes like data-line-numbers, data-trim, etc.
                code_elem = code_soup.find("code")
                if code_elem:
                    for attr_name, attr_value in elem.attrs.items():
                        # Copy all data-* attributes except data-load-code
                        if (
                            attr_name.startswith("data-")
                            and attr_name != "data-load-code"
                        ):
                            code_elem[attr_name] = attr_value

                elem.clear()
                elem.extend(code_soup.contents)
                del elem["data-load-code"]
                changed = True

    # If any changes were made, recursively process again for nested data-load attributes
    if changed:
        process_loads(soup, base_dir)


def build_slides(
    base_path: str,
    slides_dir: str,
    base_html_path: str,
    output_html_path: str,
    enable_live_reload: bool = False,
) -> None:
    """
    Build the complete presentation by combining base HTML with all slides.

    This function:
    1. Reads the base HTML template
    2. Discovers all slide folders (sorted alphabetically)
    3. Loads each folder's index.html
    4. Processes data-load and data-load-code attributes to inline external files
    5. Adjusts relative paths for resources
    6. Combines all slides into the base template
    7. Optionally injects live reload script
    8. Writes the final presentation to index.html

    Args:
        base_path: Root directory of the project.
        slides_dir: Directory containing slide subdirectories.
        base_html_path: Path to the base HTML template file.
        output_html_path: Path where the built index.html will be written.
        enable_live_reload: Whether to inject live reload functionality.
    """
    # Read the base HTML template
    with open(base_html_path, "r") as f:
        base_content = f.read()

    # Get all subdirectories in slides folder, sorted alphabetically
    # This ensures slides appear in a predictable order
    subfolders = sorted(
        [
            d
            for d in os.listdir(slides_dir)
            if os.path.isdir(os.path.join(slides_dir, d))
        ]
    )

    slides_content = ""
    # Extract the directory name from slides_dir for relative path construction
    slides_dir_name = os.path.basename(slides_dir)

    for folder in subfolders:
        index_path = os.path.join(slides_dir, folder, "index.html")
        if os.path.exists(index_path):
            # Load the slide's index.html
            with open(index_path, "r") as f:
                slide_content = f.read()

            # Adjust relative paths (./) to be relative to the slides folder
            # This ensures resources like images and videos load correctly
            slide_content = re.sub(
                r'src="\./([^"]*)"',
                rf'src="{slides_dir_name}/{folder}/\1"',
                slide_content,
            )
            slide_content = re.sub(
                r'data-load="\./([^"]*)"',
                rf'data-load="{slides_dir_name}/{folder}/\1"',
                slide_content,
            )
            slide_content = re.sub(
                r'data-load-code="\./([^"]*)"',
                rf'data-load-code="{slides_dir_name}/{folder}/\1"',
                slide_content,
            )

            # Process data-load attributes recursively to inline external content
            soup = BeautifulSoup(slide_content, "html.parser")
            process_loads(soup, base_path)
            slide_content = str(soup)

            # Accumulate all slide content
            slides_content += slide_content
        else:
            print(f"Warning: index.html not found in {folder}")

    # Insert all slides into the base template's .slides div
    # Use lambda to avoid issues with backslashes in LaTeX/math equations
    base_content = re.sub(
        r'(<div class="slides">)\s*</div>',
        lambda m: f"{m.group(1)}\n{slides_content}\n</div>",
        base_content,
        flags=re.DOTALL,
    )

    # Inject live reload script if enabled for development
    if enable_live_reload:
        base_content = inject_live_reload_script(base_content)

    # Write the final presentation HTML
    with open(output_html_path, "w") as f:
        f.write(base_content)

    print(f"Built index.html with slides from {len(subfolders)} folders.")


def watch_files(
    base_path: str,
    slides_dir: str,
    base_html_path: str,
    output_html_path: str,
    watch: bool,
    enable_live_reload: bool = False,
) -> None:
    """
    Watch for file changes and rebuild the presentation automatically.

    This function runs in a separate thread and continuously monitors:
    - The base HTML template
    - All files in all slide folders (recursively)
    - The slide folder structure itself

    When changes are detected, it rebuilds the presentation and optionally
    triggers a browser reload.

    Args:
        base_path: Root directory of the project.
        slides_dir: Directory containing slide subdirectories.
        base_html_path: Path to the base HTML template file.
        output_html_path: Path where the built index.html will be written.
        watch: Whether watching is enabled (exits immediately if False).
        enable_live_reload: Whether to signal browser reload on changes.
    """
    if not watch:
        return

    def get_all_files_in_slides() -> List[str]:
        """Get all files in the slides directory recursively."""
        all_files = [base_html_path]
        for root, dirs, files in os.walk(slides_dir):
            for file in files:
                file_path = os.path.join(root, file)
                all_files.append(file_path)
        return all_files

    # Initial collection of files to monitor
    files_to_watch = get_all_files_in_slides()

    # Initial collection of files to monitor
    files_to_watch = get_all_files_in_slides()

    # Record initial modification times for all watched files
    mtimes: Dict[str, float] = {
        f: os.path.getmtime(f) for f in files_to_watch if os.path.exists(f)
    }

    print(f"Watching {len(files_to_watch)} files in slides directory...")

    # Main watch loop
    while True:
        time.sleep(1)

        # Check if files were added or removed
        current_files = get_all_files_in_slides()
        if set(current_files) != set(files_to_watch):
            print("Files added or removed in slides directory, updating watch list...")
            files_to_watch = current_files

            # Update modification times for the new file list
            mtimes = {
                f: os.path.getmtime(f) for f in files_to_watch if os.path.exists(f)
            }

            # Rebuild since files changed
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

        # Check if any watched files have been modified
        changed = False
        for f in files_to_watch:
            if os.path.exists(f) and os.path.getmtime(f) != mtimes.get(f, 0):
                changed = True
                mtimes[f] = os.path.getmtime(f)
                print(f"Change detected in: {os.path.relpath(f, base_path)}")

        if changed:
            print("Rebuilding presentation...")
            build_slides(
                base_path,
                slides_dir,
                base_html_path,
                output_html_path,
                enable_live_reload,
            )
            if enable_live_reload:
                notify_reload()


def main() -> None:
    """
    Main entry point for the AutoReveal application.

    Parses command-line arguments, builds the initial presentation,
    optionally starts file watching, and launches an HTTP server to
    serve the presentation.
    """
    parser = argparse.ArgumentParser(
        description="Build and serve reveal.js presentations with automatic slide "
        "processing and optional live reload."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8085,
        help="Port to serve the presentation on (default: 8085)",
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
    parser.add_argument(
        "--slides-dir",
        type=str,
        default="slides",
        help="Directory containing slide folders (default: slides)",
    )
    args = parser.parse_args()

    # Determine paths based on script location
    base_path = os.path.dirname(os.path.abspath(__file__))
    slides_dir = os.path.join(base_path, args.slides_dir)
    base_html_path = os.path.join(base_path, "base.html")
    output_html_path = os.path.join(base_path, "index.html")

    # Perform initial build of the presentation
    build_slides(
        base_path, slides_dir, base_html_path, output_html_path, args.live_reload
    )

    # Start file watcher thread if --watch flag is enabled
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
        watcher_thread.daemon = True  # Thread will exit when main program exits
        watcher_thread.start()
        print("Watching for file changes...")

    # Change to base directory to serve files correctly
    os.chdir(base_path)

    # Choose appropriate handler based on live reload setting
    handler = (
        ReloadHTTPRequestHandler
        if args.live_reload
        else http.server.SimpleHTTPRequestHandler
    )

    # Allow socket reuse to prevent "Address already in use" errors
    socketserver.TCPServer.allow_reuse_address = True

    # Start the HTTP server
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(
            f"Serving on port {args.port}. Open http://localhost:{args.port}/index.html"
        )
        if args.live_reload:
            print("Live reload enabled - browser will auto-refresh on file changes")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
