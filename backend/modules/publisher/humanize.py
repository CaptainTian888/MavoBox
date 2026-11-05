import random


def pause(page):
    """Short UI settling delay, never used to bypass a challenge."""
    page.wait_for_timeout(random.randint(250, 750))
