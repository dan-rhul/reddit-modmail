import asyncio
import re
import time
from enum import Enum, unique, auto
import logging
from typing import Optional, Tuple, Union, List, Callable, Dict, Any

from asyncpraw.models import ModmailConversation
from asyncpraw.reddit import Subreddit, Redditor
from ruamel.yaml import YAML

from prawvents.prawvents import EventReddit

logging.basicConfig(
    level=logging.INFO
)


class Placeholders:
    types: Dict[str, Callable[[Redditor, ModmailConversation], str]]
    types = {
        "{{author}}": lambda user, modmail: user.name,
        "{{content}}": lambda user, modmail: modmail.messages[-1].body_markdown,
        "{{permalink}}": lambda user,
                                modmail: f"https://www.reddit.com/message/messages/{modmail.legacy_first_message_id}",
        "{{subreddit}}": lambda user, modmail: modmail.owner.display_name if modmail.owner is not None else "",
        # <= 2 instead of <= 1 to accommodate the new message
        "{{kind}}": lambda user, modmail: "message" if len(modmail.message) <= 2 else "reply",
        "{{subject}}": lambda user, modmail: modmail.subject
    }

    @staticmethod
    def replace_str(text: str, user: Redditor, modmail: ModmailConversation) -> str:
        replaced_str = text

        for placeholder, data_func in Placeholders.types:
            if placeholder not in replaced_str:
                continue

            replaced_str.replace(placeholder, data_func(user, modmail))

        return replaced_str


class Selectors:
    # https://github.com/reddit-archive/reddit/blob/master/r2/r2/lib/automoderator.py#L403
    types = {
        "includes-word": r"(?:^|\W|\b)%s(?:$|\W|\b)",
        "includes": u"%s",
        "starts-with": u"^%s",
        "ends-with": u"%s$",
        "full-exact": u"^%s$",
        "full-text": r"^\W*%s\W*$"
    }

    modifiers = [
        "case-insensitive",
        "case-sensitive",
        "regex"
    ]

    @staticmethod
    def match(values: List[str], text: str, selector_regex: str, modifier: str = "case-insensitive") -> bool:
        # https://github.com/reddit-archive/reddit/blob/master/r2/r2/lib/automoderator.py#L722
        match_values = values

        if modifier != "regex":
            match_values = [re.escape(value) for value in match_values]

        value_str = u"(%s)" % "|".join(match_values)
        pattern = selector_regex % value_str

        flags = re.DOTALL | re.UNICODE
        if modifier == "case-insensitive":
            flags |= re.IGNORECASE

        match_pattern = re.compile(pattern, flags)

        return bool(match_pattern.search(text))


class Thresholds:
    comparators = {
        ">": lambda value, to_compare: value > to_compare,
        "<": lambda value, to_compare: value < to_compare
    }
    time_units = {
        "minutes": 60 * 1000,
        "hours": 60 * 60 * 1000,
        "days": 24 * 60 * 60 * 1000,
        "weeks": 7 * 24 * 60 * 60 * 1000,
        "months": 30 * 7 * 24 * 60 * 60 * 1000,
        "years": 12 * 30 * 7 * 24 * 60 * 60 * 1000
    }

    @staticmethod
    def parse(threshold_text: str) -> Optional['Thresholds']:
        # minimum: >1, <1
        # should be at least 2 letters, the comparator and value
        if len(threshold_text) <= 2:
            return None

        comparator = threshold_text[0]
        if comparator not in Thresholds.comparators.keys():
            return None

        value_split = threshold_text[1:].strip().split(" ", 1)

        try:
            value = int(value_split[0])
        except TypeError as error:
            logging.error(f"Failed to parse threshold text: {threshold_text}.", error)
            return None

        time_unit = Thresholds.time_units.get(value_split[1])
        if time_unit is not None:
            value = value * time_unit

        return Thresholds(comparator, value)

    def __init__(self, comparator: str, value: int):
        self.comparator = comparator
        self.value = value

    def satisfies(self, value: int, is_time: bool = False) -> bool:
        if is_time:
            value = value + (time.time_ns() // 1000000)

        return Thresholds.comparators[self.comparator](value, self.value)


@unique
class Rule(Enum):
    ACTION = auto()
    COMMENT = auto()

    CONTENT = auto()
    SUBJECT = auto()

    CONTENT_LONGER_THAN = auto()
    CONTENT_SHORTER_THAN = auto()

    AUTHOR_POST_KARMA = auto()
    AUTHOR_COMMENT_KARMA = auto()
    AUTHOR_COMBINED_KARMA = auto()
    AUTHOR_ACCOUNT_AGE = auto()
    AUTHOR_HAS_VERIFIED_EMAIL = auto()

    AUTHOR_IS_CONTRIBUTOR = auto()
    AUTHOR_IS_MODERATOR = auto()

    AUTHOR_SATISFY_ANY_THRESHOLD = auto()

    IS_TOP_LEVEL = auto()
    TYPE = auto()

    def as_yaml_key(self) -> str:
        if str.startswith(self.name, "AUTHOR_"):
            key = str.replace(self.name, "AUTHOR_", "author.")
        else:
            key = self.name

        key = str.lower(key)
        return key

    def get_priority(self) -> int:
        return self.value

    @staticmethod
    def is_required(rule: 'Rule') -> bool:
        return rule in [Rule.TYPE, Rule.CONTENT, Rule.SUBJECT, Rule.ACTION]

    @staticmethod
    def get_required_num() -> int:
        # TYPE, ACTION and CONTENT or SUBJECT
        return 3


class RuleActions:
    @staticmethod
    def parse(rules: List[Tuple[str, Union[str, int, bool, List[str]]]]) -> Optional['RuleActions']:
        rules_found: List[Tuple[Rule, Optional[str], list]]
        rules_found = []

        for key, value in rules:
            rule_key = key
            rule_value = value

            if not isinstance(rule_value, bool) and len(rule_value) <= 0:
                continue

            selector: Optional[str]
            selector = None

            if "(" in rule_key and ")" in rule_key:
                _rule_key_split = rule_key.split("(", 1)
                selector = _rule_key_split[1].replace(")", "", 1).strip()
                rule_key = _rule_key_split[0].strip()

            rule = None
            for rule_obj in Rule:
                if rule_obj.as_yaml_key() == rule_key:
                    rule = rule_obj
                    break

            if rule is None:
                continue

            if rule == Rule.COMMENT and isinstance(rule_value, str) and "\\n" in rule_value:
                rule_value = rule_value.split("\\n")

            if not isinstance(rule_value, list):
                rule_value = [rule_value]

            # removes elements from found rules if it is already in the list
            # this is to replicate the behaviour of auto mod, where the latter rule is used if there are duplicate
            # rules
            rules_found = [rule_found for rule_found in rules_found if rule_found[0] != rule]

            rules_found.append((rule, selector, rule_value))

        if len(rules_found) < Rule.get_required_num():
            return None

        if len([rule_found for rule_found in rules_found if Rule.is_required(rule_found[0])]) < Rule.get_required_num():
            return None

        rules_found.sort(key=lambda action_tuple: action_tuple[0].value, reverse=True)

        return RuleActions(rules_found)

    def __init__(self, actions: List[Tuple[Rule, Optional[str], list]]):
        self.actions = actions

    async def should_action(self, conversation: ModmailConversation, subreddit: Subreddit, state: str) -> bool:
        author_satisfy_any_threshold = False
        author_satisfied = False

        last_message = conversation.messages[-1]

        # actions list is sorted by rule priority already
        for action in self.actions:
            rule = action[0]
            selector = action[1]
            values = action[2]

            if rule == Rule.TYPE and state not in values:
                continue

            if rule == Rule.IS_TOP_LEVEL and len(conversation.messages) > 1:
                continue

            # author related rules
            if rule == Rule.AUTHOR_SATISFY_ANY_THRESHOLD:
                author_satisfy_any_threshold = True
                continue

            if rule.name.startswith("AUTHOR_"):
                if author_satisfy_any_threshold and author_satisfied:
                    continue

                # further author rules require re-fetching author object
                await last_message.author.load()

                if rule == Rule.AUTHOR_IS_MODERATOR and values[0]:
                    is_moderator = False

                    async for moderator in subreddit.moderator:
                        if moderator.id == last_message.author.fullname:
                            is_moderator = True
                            break

                    author_satisfied = is_moderator
                elif rule == Rule.AUTHOR_IS_CONTRIBUTOR and values[0]:
                    is_contributor = False

                    async for contributor in subreddit.contributor:
                        if contributor.id == last_message.author.fullname:
                            is_contributor = True
                            break

                    author_satisfied = is_contributor
                elif rule == Rule.AUTHOR_HAS_VERIFIED_EMAIL and values[0]:
                    author_satisfied = last_message.author.has_verified_email
                else:
                    threshold = Thresholds.parse(values[0])
                    if threshold is None:
                        return False

                    if rule == Rule.AUTHOR_ACCOUNT_AGE:
                        author_satisfied = threshold.satisfies(last_message.author.created_utc, True)
                    elif rule == Rule.AUTHOR_COMBINED_KARMA:
                        combined_karma = last_message.author.comment_karma + last_message.author.link_karma

                        author_satisfied = threshold.satisfies(combined_karma)
                    elif rule == Rule.AUTHOR_COMMENT_KARMA:
                        author_satisfied = threshold.satisfies(last_message.author.comment_karma)
                    elif rule == Rule.AUTHOR_POST_KARMA:
                        author_satisfied = threshold.satisfies(last_message.author.link_karma)

                if not author_satisfy_any_threshold and not author_satisfied:
                    return False

            if rule == Rule.CONTENT_SHORTER_THAN and len(last_message.body_markdown) < values[0]:
                return False

            if rule == Rule.CONTENT_LONGER_THAN and len(last_message.body_markdown) > values[0]:
                return False

            if rule == Rule.SUBJECT or rule == Rule.CONTENT:
                if rule == Rule.SUBJECT:
                    text = conversation.subject
                else:
                    text = last_message.body_markdown

                modifier = None
                if "," in selector:
                    _selector_split = selector.split(",", 1)
                    modifier = _selector_split[1].strip()
                    selector_regex = Selectors.types[_selector_split[0].strip()]
                else:
                    selector_regex = Selectors.types[selector]

                if not Selectors.match(values, text, selector_regex, modifier):
                    return False

        return True

    async def run(self, conversation: ModmailConversation) -> bool:
        actioned = False

        for action in self.actions:
            rule = action[0]
            values = action[2]

            if rule not in [Rule.ACTION, Rule.COMMENT]:
                continue

            if rule == Rule.COMMENT:
                author = await conversation.messages[-1].author.load()
                body = Placeholders.replace_str("\n".join(values), author, conversation)
                await conversation.reply(body)
                actioned = True
                continue

            if rule == Rule.ACTION:
                action_values = sorted(values)
                for value in action_values:
                    if value == "highlight":
                        if not conversation.is_highlighted:
                            await conversation.highlight()
                        actioned = True
                        continue

                    if value == "remove":
                        if conversation.is_highlisted:
                            await conversation.unhighlight()
                        await conversation.archive()
                        actioned = True

        return actioned

    def get_action(self, rule: Rule) -> Tuple[Rule, Optional[str], list]:
        for action in self.actions:
            if action[0] == rule:
                return action

        raise KeyError("No action was found for rule " + rule.name)


class RedditModmail:
    def __init__(self):
        self.reddit = EventReddit("bot", config_interpolation="basic")
        self.yaml = YAML()

        self.loop = asyncio.get_event_loop()

        self.has_listened = False

    async def listen(self, subreddit: str):
        logging.info("Listening to modmail from subreddit: " + subreddit)
        subreddit_model = await self.reddit.subreddit(subreddit)
        if subreddit_model is None:
            logging.error(f"Failed to find subreddit {subreddit}.")
            return

        for state in ["all", "appeals", "join_requests", "mod", "notifications"]:
            @self.reddit.register_event(
                subreddit_model.mod.stream.modmail_conversations,
                sort="recent",
                state=state,
                skip_existing=True
            )
            async def on_message(message: ModmailConversation, message_state=state):
                actioned = await self.handle_message(subreddit_model, message, message_state)
                logging.info(f"Result of handling message id {message.id} for subreddit {subreddit} from state "
                             f"{message_state}: {actioned}.")

        if not self.has_listened:
            self.has_listened = True
            await self.reddit.run_loop()

    async def handle_message(self, subreddit: Subreddit, message: ModmailConversation, state: str) -> bool:
        await message.load()
        if message.
        logging.info(f"Handling message id {message.id} for subreddit {subreddit.display_name} from state {state}.")

        config = self.parse_config_yaml(await self.get_config(subreddit, "reddit_modmail"))
        rules = self.get_rules_of_type(state, config)
        if len(rules) == 0:
            return False

        for rule in rules:
            rule_action = RuleActions.parse(list(rule.items()))  # type: ignore
            if rule_action is None or not await rule_action.should_action(message, subreddit, state):
                continue

            if rule_action.run(message):
                return True

        return False

    def get_rules_of_type(self, rule_type: str, rules: List[Dict[str, Union[str, int, bool, List[str]]]]) -> []:
        type_rules = []

        for rule in rules:
            type_value = rule.get(Rule.TYPE.as_yaml_key())
            if type_value is None:
                continue

            if isinstance(type_value, str) and type_value != rule_type:
                continue

            if isinstance(type_value, list) and rule_type not in type_value:
                continue

            type_rules.append(rule)

        return type_rules

    async def get_config(self, subreddit: Subreddit, wiki_page: str) -> str:
        return (await subreddit.wiki.get_page(wiki_page)).content_md

    def parse_config_yaml(self, text: str) -> List[Dict[str, Union[str, int, bool, List[str]]]]:
        # https://github.com/reddit-archive/reddit/blob/master/r2/r2/lib/automoderator.py#L209
        rules: List[Dict[str, Union[str, int, bool, List[str]]]]
        rules = []

        sections = [section.strip("\r\n") for section in re.split("^---", text, flags=re.MULTILINE)]
        for section in sections:
            try:
                parsed = self.yaml.load(section)
            except Exception as error:
                raise ValueError(f"YAML parse error: {section}", error)

            if not isinstance(parsed, dict):
                continue

            section_rules: List[Tuple[str, Union[str, int, bool, List[str]]]]
            section_rules = []
            for key in parsed:
                value = parsed[key]

                # it is a nested value, i.e:
                # author: {
                #   is_moderator: True
                # }
                if isinstance(value, dict):
                    key_prefix = key + "."
                    for inner_key in value.keys():
                        inner_value = value[inner_key]

                        if self._check_parsed_value(inner_value):
                            section_rules.append((key_prefix + inner_key, inner_value))
                    continue

                if self._check_parsed_value(value):
                    section_rules.append((key, value))

            if len(section_rules) <= 0:
                continue

            rules_dict = {}
            for section_rule_name, section_rule_value in section_rules:
                rules_dict[section_rule_name] = section_rule_value

            rules.append(rules_dict)

        return rules

    def _check_parsed_value(self, value: Any) -> bool:
        if value is None:
            return False

        return isinstance(value, (str, int, bool, list))
