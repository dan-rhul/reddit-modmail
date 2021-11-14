import asyncio

from RedditModmail import RedditModmail


async def main():
    reddit_modmail = None

    try:
        reddit_modmail = RedditModmail()

        with open("subreddits.txt") as subreddits_file:
            for subreddit_str in subreddits_file:
                await reddit_modmail.listen(subreddit_str.strip())
    finally:
        if reddit_modmail is not None:
            await reddit_modmail.reddit.close()


if __name__ == '__main__':
    asyncio.run(main())
