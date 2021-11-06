import asyncio
from enum import Enum, unique, auto
from threading import Thread
import logging
from typing import Optional, Tuple, Union, OrderedDict

import asyncpraw
from asyncpraw.models import ModmailConversation
from asyncpraw.reddit import Subreddit
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMapItemsView

logging.basicConfig(
    level=logging.INFO
)


@unique
class Rule(Enum):
    ACTION: auto()
    COMMENT: auto()

    CONTENT: auto()
    SUBJECT: auto()

    AUTHOR_POST_KARMA: auto()
    AUTHOR_COMMENT_KARMA: auto()
    AUTHOR_COMBINED_KARMA: auto()
    AUTHOR_ACCOUNT_AGE: auto()
    AUTHOR_SATISFY_ANY_THRESHOLD: auto()

    CONTENT_LONGER_THAN: auto()
    CONTENT_SHORTER_THAN: auto()

    AUTHOR_IS_CONTRIBUTOR: auto()
    AUTHOR_IS_MODERATOR: auto()

    IS_TOP_LEVEL: auto()
    TYPE: auto()

    def as_yaml_key(self) -> str:
        if str.startswith(self.name, "AUTHOR"):
            key = str.replace(self.name, "AUTHOR_", "author.")
        else:
            key = self.name

        key = str.lower(key)
        return key

    def get_priority(self) -> int:
        return self.value


class RuleActions:
    @staticmethod
    def parse(conditions: Tuple[str, Union[str, OrderedDict]]) -> 'RuleActions':
        pass

    def __init__(self):
        self.actions = []

    def should_action(self) -> bool:
        pass

    def run(self):
        pass


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
            await self.handle_message(subreddit, message, state)

    async def handle_message(self, subreddit: Subreddit, message: ModmailConversation, state: str):
        conversation = await subreddit.modmail(message.id)
        lastMessage = conversation.messages[-1].body_markdown

        config = self.parse_config_yaml(await self.get_config(subreddit, "reddit_modmail"))
        rules = self.get_rules_of_type(state, config)
        if len(rules) == 0:
            return

        rule: Optional[Rule]
        rule = None
        for rule_pair in rules:
            rule = Rule.parse(rule_pair[1].items())

        if rule is None:
            return

        if rule.should_action():
            rule.run()

    def get_rules_of_type(self, rule_type: str, rules: CommentedMapItemsView) -> []:
        type_rules = []

        for item in rules:
            for key, value in item[1].items():
                if key == "type" and (isinstance(value, str) and value == rule_type) or (isinstance(value, list) and rule_type in value):
                    type_rules.append(item)
                    break

        return type_rules

    async def get_config(self, subreddit: Subreddit, wiki_page: str) -> str:
        return await subreddit.wiki.get_page(wiki_page).content_md

    def parse_config_yaml(self, text: str) -> CommentedMapItemsView:
        return self.yaml.load(text).items()
