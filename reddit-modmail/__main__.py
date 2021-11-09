import asyncio

from RedditModmail import RedditModmail


async def main():
    reddit_modmail = None

    try:
        reddit_modmail = RedditModmail()
        # TODO: read config file and listen to subreddits listed
    finally:
        if reddit_modmail is not None:
            await reddit_modmail.reddit.close()


if __name__ == '__main__':
    asyncio.run(main())
