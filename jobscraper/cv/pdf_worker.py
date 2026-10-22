"""python -m jobscraper.cv.pdf_worker <cv.html> <out.pdf> — run by render.write_pdf in its own process."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(html_path: Path, pdf_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            # The HTML is self-contained (inline CSS, no scripts, no external resources)
            page.set_content(html_path.read_text(encoding="utf-8"), wait_until="load")
            page.pdf(path=str(pdf_path), format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
