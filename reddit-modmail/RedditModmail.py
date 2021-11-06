import asyncio
from enum import Enum, unique, auto
from threading import Thread
import logging
from typing import Optional, Tuple, Union, OrderedDict, List

import asyncpraw
from asyncpraw.models import ModmailConversation
from asyncpraw.models.reddit.subreddit import Modmail
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

    def is_required(self) -> bool:
        match self:
            case self.TYPE | self.CONTENT | self.SUBJECT | self.ACTION:
                return True
            case _:
                return False

    @staticmethod
    def get_required_num() -> int:
        # TYPE, ACTION and CONTENT or SUBJECT
        return 3


class RuleActions:
    @staticmethod
    def parse(rules: Tuple[str, Union[str, OrderedDict]]) -> Optional['RuleActions']:
        rules_found: List[Tuple[Rule, Optional[str], Union[str, OrderedDict]]]
        rules_found = []

        for rule in Rule:
            rule_key: str
            rule_value: Union[str, OrderedDict]
            for rule_key, rule_value in rules:
                selector: Optional[str]
                selector = None

                if "(" in rule_key and ")" in rule_key:
                    _rule_key_split = str.split(rule_key, "(", 1)
                    selector = str.strip(_rule_key_split[1].replace(")", "", 1))
                    rule_key = rule_key.strip(_rule_key_split[0])

                if rule.as_yaml_key() != rule_key:
                    continue

                # removes elements from found rules if it is already in the list
                # this is to replicate the behaviour of auto mod, where the latter rule is used if there are duplicate
                # rules
                rules_found = [(list_rule, list_selector, list_value) for (list_rule, list_selector, list_value) in rules_found if list_rule != rule]

                rules_found.append((rule, selector, rule_value))

        if len(rules_found) < Rule.get_required_num():
            return None

        return RuleActions(rules_found)

    def __init__(self, actions: List[Tuple[Rule, Optional[str], Union[str, OrderedDict]]]):
        self.actions = actions

    def should_action(self, conversation: Modmail, state: str) -> bool:
        pass

    def run(self, conversation: Modmail, state: str) -> bool:
        pass


class RedditModmail:
    def __init__(self):
        self.reddit = asyncpraw.Reddit("bot", config_interpolation="basic")
        self.yaml = YAML()

        self.loop = asyncio.get_event_loop()

    def listen(self, subreddit: str):
        t = Thread(target=lambda: (
            self.loop.create_task(self.__listen(subreddit)),
            self.loop.run_forever())
                   )
        t.start()

    async def __listen(self, subreddit: str):
        logging.info("Listening to modmail from subreddit: " + subreddit)
        subreddit_model = await self.reddit.subreddit(subreddit)

        await asyncio.gather(*map(
            lambda state: self.__listen_state(subreddit_model, state),
            ["appeals", "archived", "inprogress", "join_requests", "mod", "new", "notifications"]
        ))

    async def __listen_state(self, subreddit: Subreddit, state: str):
        async for message in subreddit.mod.stream.modmail_conversations(sort="recent",
                                                                        state=state):
            await self.handle_message(subreddit, message, state)

    async def handle_message(self, subreddit: Subreddit, message: ModmailConversation, state: str) -> bool:
        conversation = await subreddit.modmail(message.id)

        config = self.parse_config_yaml(await self.get_config(subreddit, "reddit_modmail"))
        rules = self.get_rules_of_type(state, config)
        if len(rules) == 0:
            return False

        for rule_pair in rules:
            rule_action = RuleActions.parse(rule_pair[1].items())
            if rule_action is None or not rule_action.should_action(conversation, state):
                continue

            rule_action.run(conversation, state)
            return True

        return False

    def get_rules_of_type(self, rule_type: str, rules: CommentedMapItemsView) -> []:
        type_rules = []

        for item in rules:
            for key, value in item[1].items():
                if key == Rule.TYPE.as_yaml_key() and (isinstance(value, str) and value == rule_type) or (isinstance(value, list) and rule_type in value):
                    type_rules.append(item)
                    break

        return type_rules

    async def get_config(self, subreddit: Subreddit, wiki_page: str) -> str:
        return await subreddit.wiki.get_page(wiki_page).content_md

    def parse_config_yaml(self, text: str) -> CommentedMapItemsView:
        return self.yaml.load(text).items()
