from playwright.sync_api import sync_playwright
from urllib.parse import urljoin
from datetime import datetime
import csv
import time
import os
import re
import random

# ============================================================
# CONFIGURATION
# ============================================================

FINAL_DATA_FILE = "drdo_xinhua_dataset.csv"

TARGET_URLS = [
    "https://english.news.cn/list/china-chinaworld.htm",
    "https://english.news.cn/list/china-politics.htm"
]

# GitHub Actions will stop a job at 6 hours.
# We stop ourselves at 5.5 hours so the CSV can be safely
# uploaded before the runner is terminated.
MAX_RUNTIME_SECONDS = 5.5 * 60 * 60

# Created only when BOTH target archives are completely scraped.
COMPLETION_FILE = "xh_scraper_complete.flag"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def human_delay(min_sec=1, max_sec=3):
    """Randomized delay between browser actions."""
    time.sleep(random.uniform(min_sec, max_sec))


def time_limit_reached(start_time):
    """
    Checks whether the current GitHub Actions run has reached
    the 5.5-hour safety limit.
    """

    elapsed = (datetime.now() - start_time).total_seconds()

    if elapsed >= MAX_RUNTIME_SECONDS:
        print("\n============================================================")
        print("[TIME LIMIT] 5.5-hour runtime limit reached.")
        print("[SAVE] All successfully scraped articles were already saved.")
        print("[STOP] Exiting cleanly for the next GitHub Actions run.")
        print("============================================================\n")

        return True

    return False


def get_scraped_urls():
    """
    Reads the existing CSV and returns all URLs that have
    already been processed.

    This allows the scraper to resume after a GitHub Actions
    runner is replaced.
    """

    scraped = set()

    if not os.path.exists(FINAL_DATA_FILE):
        return scraped

    try:
        with open(
            FINAL_DATA_FILE,
            mode="r",
            encoding="utf-8",
            newline=""
        ) as file:

            reader = csv.reader(file)

            for row in reader:

                # URL is column index 7.
                if len(row) >= 8 and row[0] != "source_id":

                    url = row[7].strip()

                    if url:
                        scraped.add(url)

    except Exception as e:
        print(f"[WARNING] Could not read existing CSV: {e}")

    return scraped


def save_to_csv(source_id, date, title, text, url):
    """
    Saves one article using the exact 11-column
    DRDO-1 Stage 1 dataset format.
    """

    file_exists = os.path.exists(FINAL_DATA_FILE)

    with open(
        FINAL_DATA_FILE,
        mode="a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        # Create header only when the file does not exist.
        if not file_exists:

            writer.writerow([
                "source_id",
                "source_type",
                "language",
                "country_of_origin",
                "publication_date",
                "title",
                "full_text",
                "url",
                "translated_text",
                "reliability_metadata",
                "document_topic_tags"
            ])

        writer.writerow([
            source_id,
            "State Media",
            "English",
            "China",
            date,
            title,
            text,
            url,
            "N/A - Original in English",
            "Pending LLM Enrichment",
            "Pending LLM Enrichment"
        ])


# ============================================================
# MAIN SCRAPER
# ============================================================

def main():

    start_time = datetime.now()

    # --------------------------------------------------------
    # Check whether the entire scraper already finished.
    # --------------------------------------------------------

    if os.path.exists(COMPLETION_FILE):

        print("[COMPLETE] Scraper has already completed.")
        print("[COMPLETE] Nothing more to scrape.")

        return True

    # --------------------------------------------------------
    # Load previously scraped URLs.
    # --------------------------------------------------------

    scraped_urls = get_scraped_urls()

    print(
        f"Loaded {len(scraped_urls)} "
        f"previously scraped articles from memory."
    )

    # --------------------------------------------------------
    # Start Playwright.
    # --------------------------------------------------------

    with sync_playwright() as p:

        browser = None
        list_page = None

        try:

            # ------------------------------------------------
            # Launch Chromium.
            # ------------------------------------------------

            browser = p.chromium.launch(
                headless=True
            )

            context = browser.new_context(

                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/115.0.0.0 "
                    "Safari/537.36"
                ),

                viewport={
                    "width": 1366,
                    "height": 768
                },

                locale="en-US"
            )

            # ------------------------------------------------
            # MASTER TAB
            # ------------------------------------------------

            list_page = context.new_page()

            # Continue numbering from the existing dataset.
            global_article_counter = len(scraped_urls) + 1

            # =================================================
            # PROCESS BOTH XINHUA TARGET LISTS
            # =================================================

            for list_index, list_url in enumerate(TARGET_URLS, start=1):

                print("\n")
                print("============================================================")
                print(f"[TARGET {list_index}/{len(TARGET_URLS)}]")
                print(f"{list_url}")
                print("============================================================")

                # ------------------------------------------------
                # Check runtime before starting another category.
                # ------------------------------------------------

                if time_limit_reached(start_time):

                    print("[PAUSED] Current GitHub Actions run ended.")
                    return False

                try:

                    # ------------------------------------------------
                    # Open category page.
                    # ------------------------------------------------

                    print("[NAVIGATION] Opening category...")

                    list_page.goto(
                        list_url,
                        wait_until="domcontentloaded",
                        timeout=60000
                    )

                    try:

                        list_page.wait_for_load_state(
                            "networkidle",
                            timeout=30000
                        )

                    except Exception:

                        print(
                            "[WARNING] networkidle timeout. "
                            "Continuing anyway."
                        )

                    # ------------------------------------------------
                    # Pagination state.
                    # ------------------------------------------------

                    click_cycle = 0
                    stagnant_cycles = 0

                    # =================================================
                    # PAGINATION LOOP
                    # =================================================

                    while True:

                        # ------------------------------------------------
                        # Runtime check.
                        # ------------------------------------------------

                        if time_limit_reached(start_time):

                            print(
                                "[PAUSED] Exiting before another "
                                "pagination cycle."
                            )

                            return False

                        click_cycle += 1

                        print(
                            f"\n[CYCLE {click_cycle}] "
                            f"Sweeping visible articles..."
                        )

                        # ------------------------------------------------
                        # Find article links.
                        # ------------------------------------------------

                        article_elements = list_page.locator(
                            ".item .tit a, a[href*='/202']"
                        ).all()

                        print(
                            f"[FOUND] {len(article_elements)} "
                            f"visible article links."
                        )

                        new_articles_found = 0

                        # =================================================
                        # PROCESS EACH ARTICLE
                        # =================================================

                        for element in article_elements:

                            # ------------------------------------------------
                            # Runtime check before every article.
                            # ------------------------------------------------

                            if time_limit_reached(start_time):

                                print(
                                    "[PAUSED] Stopping before processing "
                                    "another article."
                                )

                                return False

                            # ------------------------------------------------
                            # Extract href.
                            # ------------------------------------------------

                            raw_href = element.get_attribute("href")

                            if not raw_href:
                                continue

                            # ------------------------------------------------
                            # Convert relative URL → absolute URL.
                            # ------------------------------------------------

                            full_url = urljoin(
                                list_page.url,
                                raw_href
                            )

                            # ------------------------------------------------
                            # Skip already-scraped URLs.
                            # ------------------------------------------------

                            if full_url in scraped_urls:

                                continue

                            print(
                                f"   -> [TARGET ACQUIRED] {full_url}"
                            )

                            new_articles_found += 1

                            article_tab = None

                            try:

                                # =================================================
                                # OPEN ARTICLE
                                # =================================================

                                article_tab = context.new_page()

                                article_tab.goto(
                                    full_url,
                                    wait_until="domcontentloaded",
                                    timeout=45000
                                )

                                human_delay(1, 2)

                                # =================================================
                                # TITLE
                                # =================================================

                                title_element = article_tab.locator(
                                    "h1, .title, .header-title"
                                ).first

                                if title_element.count() > 0:

                                    title = (
                                        title_element
                                        .text_content()
                                        .strip()
                                    )

                                else:

                                    title = "Unknown Title"

                                # =================================================
                                # DATE
                                # =================================================

                                date_element = article_tab.locator(
                                    ".time, .info, .year, .date"
                                ).first

                                if date_element.count() > 0:

                                    raw_date = (
                                        date_element
                                        .text_content()
                                        .strip()
                                    )

                                else:

                                    raw_date = "Unknown Date"

                                date_match = re.search(
                                    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
                                    raw_date
                                )

                                if date_match:

                                    clean_date = date_match.group(0)

                                else:

                                    clean_date = raw_date

                                # =================================================
                                # FULL ARTICLE TEXT
                                # =================================================

                                paragraphs = article_tab.locator(
                                    ".content p, #detail p, .main-content p"
                                ).all_inner_texts()

                                full_text = " ".join(
                                    p.strip()
                                    for p in paragraphs
                                    if p.strip()
                                )

                                # ------------------------------------------------
                                # Fallback extraction.
                                # ------------------------------------------------

                                if not full_text:

                                    paragraphs = article_tab.locator(
                                        "p"
                                    ).all_inner_texts()

                                    full_text = " ".join(
                                        p.strip()
                                        for p in paragraphs
                                        if len(p.strip()) > 30
                                    )

                                # =================================================
                                # VALIDATE AND SAVE
                                # =================================================

                                if title and full_text:

                                    source_id = (
                                        f"XH_{global_article_counter:03d}"
                                    )

                                    save_to_csv(
                                        source_id,
                                        clean_date,
                                        title,
                                        full_text,
                                        full_url
                                    )

                                    scraped_urls.add(full_url)

                                    global_article_counter += 1

                                    print(
                                        f"      [SUCCESS] "
                                        f"Saved as {source_id}"
                                    )

                                else:

                                    print(
                                        "      [FAILED] "
                                        "Could not extract article text."
                                    )

                                    # ------------------------------------------------
                                    # Mark failed URL as processed for this run.
                                    # ------------------------------------------------

                                    scraped_urls.add(full_url)

                            except Exception as e:

                                print(
                                    f"      [ERROR] Failed on "
                                    f"{full_url}: "
                                    f"{str(e)[:150]}"
                                )

                            finally:

                                # ------------------------------------------------
                                # Always close article tab.
                                # ------------------------------------------------

                                if article_tab is not None:

                                    try:
                                        article_tab.close()

                                    except Exception:
                                        pass

                        # =================================================
                        # CHECK WHETHER NEW ARTICLES WERE FOUND
                        # =================================================

                        if new_articles_found > 0:

                            stagnant_cycles = 0

                        else:

                            print(
                                "   -> No new articles found "
                                "in this cycle."
                            )

                            stagnant_cycles += 1

                            print(
                                f"   -> Stagnant cycle "
                                f"{stagnant_cycles}/5"
                            )

                            if stagnant_cycles >= 5:

                                print(
                                    " [END] No new articles appeared "
                                    "after 5 consecutive sweeps."
                                )

                                break

                        # =================================================
                        # RUNTIME CHECK BEFORE PAGINATION
                        # =================================================

                        if time_limit_reached(start_time):

                            print(
                                "[PAUSED] Stopping before pagination."
                            )

                            return False

                        # =================================================
                        # PAGINATION
                        # =================================================

                        print(
                            " [PAGINATION] Attempting to click "
                            "the 'More' button..."
                        )

                        more_btn = list_page.locator(
                            "div#more.list-more"
                        )

                        # ------------------------------------------------
                        # Check visibility.
                        # ------------------------------------------------

                        try:

                            is_visible = more_btn.is_visible()

                        except Exception:

                            is_visible = False

                        if is_visible:

                            try:

                                # ------------------------------------------------
                                # Scroll to button.
                                # ------------------------------------------------

                                more_btn.scroll_into_view_if_needed()

                                human_delay(3, 5)

                                # ------------------------------------------------
                                # Click.
                                # ------------------------------------------------

                                more_btn.click(force=True)

                                print(
                                    " [PAGINATION] Clicked 'More'. "
                                    "Waiting for new articles..."
                                )

                                # ------------------------------------------------
                                # Wait for Vue/content to render.
                                # ------------------------------------------------

                                list_page.wait_for_timeout(
                                    10000
                                )

                            except Exception as e:

                                print(
                                    " [CRITICAL ERROR] "
                                    f"Failed to click 'More': {e}"
                                )

                                break

                        else:

                            print(
                                " [END] 'More' button is no longer "
                                "visible. End of archive reached."
                            )

                            break

                except Exception as e:

                    print(
                        f"[CRITICAL ERROR] Failed processing list "
                        f"{list_url}: {e}"
                    )

                    # ------------------------------------------------
                    # Do NOT mark scraper complete.
                    # The next GitHub Actions run will retry.
                    # ------------------------------------------------

                    return False

            # ============================================================
            # BOTH TARGETS COMPLETED
            # ============================================================

            print("\n")
            print("============================================================")
            print("[COMPLETE] Xinhua collection cycle finished.")
            print("[COMPLETE] All target categories were processed.")
            print("============================================================")

            # ------------------------------------------------------------
            # Create completion marker.
            # GitHub workflow uses this to know whether another
            # run is required.
            # ------------------------------------------------------------

            with open(
                COMPLETION_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                file.write(
                    "Xinhua scraping completed successfully.\n"
                )

            return True

        finally:

            # ============================================================
            # CLEAN SHUTDOWN
            # ============================================================

            if list_page is not None:

                try:
                    list_page.close()

                except Exception:
                    pass

            if browser is not None:

                try:
                    browser.close()

                except Exception:
                    pass


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":

    completed = main()

    if completed:

        print(
            "\n[FINAL STATUS] "
            "SCRAPER COMPLETE."
        )

    else:

        print(
            "\n[FINAL STATUS] "
            "SCRAPER PAUSED FOR NEXT RUN."
        )