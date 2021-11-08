import asyncio
import logging

from RedditModmail import RedditModmail


async def main():
    reddit_modmail = None

    try:
        reddit_modmail = RedditModmail()
        # TODO: read config file and listen to subreddits listed
    except Exception as error:
        logging.error(error)
    finally:
        if reddit_modmail is not None:
            await reddit_modmail.reddit.close()


if __name__ == '__main__':
    asyncio.run(main())
