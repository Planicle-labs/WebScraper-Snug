import urllib.robotparser
import urllib.parse
import urllib.request
import urllib.error
from core.logger import logger


def check_robots(base_url, target_url, user_agent="*"):
    """
    Checks if scraping the target_url is allowed by the robots.txt of base_url.
    Returns:
        is_allowed (bool): True if scraping is allowed.
        delay (float): The crawl delay specified, or 0.0 if not specified.
    """
    try:
        parsed_url = urllib.parse.urlparse(base_url)
        robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)

        # Fetch with a timeout so it doesn't hang indefinitely on bad connections
        req = urllib.request.urlopen(robots_url, timeout=10)
        rp.parse(req.read().decode("utf-8").splitlines())

        is_allowed = rp.can_fetch(user_agent, target_url)
        delay = rp.crawl_delay(user_agent) or 0.0

        return is_allowed, delay

    except urllib.error.URLError as e:
        # Standard behavior: if robots.txt is missing (e.g. 404), crawling is allowed.
        logger.info(
            f"Could not fetch robots.txt for {base_url} (HTTP Error: {e}). Defaulting to ALLOWED."
        )
        return True, 0.0
    except Exception as e:
        logger.error(f"Unexpected error parsing robots.txt for {base_url}: {e}")
        # Defaulting to blocked on unexpected error just to be safe
        return False, 0.0
