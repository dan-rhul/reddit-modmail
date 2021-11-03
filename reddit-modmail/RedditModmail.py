import asyncio
from threading import Thread
import logging

import asyncpraw
from asyncpraw.models import ModmailConversation
from asyncpraw.reddit import Subreddit
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMapItemsView

logging.basicConfig(
    level=logging.INFO
)


class RedditModmail:
    def __init__(self):
        self.reddit = asyncpraw.Reddit("bot", config_interpolation="basic")
        self.yaml = YAML()

        self.loop = asyncio.get_event_loop()

    def listen(self, subreddit: str):
        t = Thread(target=lambda: (
            self.loop.create_task(self._listen(subreddit)),
            self.loop.run_forever())
                   )
        t.start()

    async def _listen(self, subreddit: str):
        logging.info("Listening to modmail from subreddit: " + subreddit)
        subreddit_model = await self.reddit.subreddit(subreddit)

        await asyncio.gather(*map(
            lambda state: self._listen_state(subreddit_model, state),
            ["appeals", "archived", "inprogress", "join_requests", "mod", "new", "notifications"]
        ))

    async def _listen_state(self, subreddit: Subreddit, state: str):
        async for message in subreddit.mod.stream.modmail_conversations(sort="recent",
                                                                        state=state):
            self.handle_message(subreddit, message, state)

    def handle_message(self, subreddit: Subreddit, message: ModmailConversation, state: str):
        pass

    async def get_config(self, subreddit: Subreddit, wiki_page: str) -> str:
        return await subreddit.wiki.get_page(wiki_page).content_md

    def parse_config_yaml(self, text: str) -> CommentedMapItemsView:
        return self.yaml.load(text).items()
        # for item in self.yaml.load(text).items():
        #     print(item[0])
        #     for rule, value in item[1].items():
        #         print(rule)
        #         print(value)
