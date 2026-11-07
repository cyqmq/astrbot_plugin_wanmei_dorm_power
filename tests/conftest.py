"""测试夹具：注入最小化 astrbot 假模块，使 main.py 可在无 AstrBot 环境下导入。"""
import importlib
import sys
import types


class FakeLogger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class FakeAstrBotConfig:
    """模拟配置对象，提供 get / save_config / __setitem__。"""

    def __init__(self, data=None):
        self._data = dict(data or {})
        self.saved = False

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def values(self):
        return self._data.values()

    def save_config(self):
        self.saved = True


class FakeEvent:
    """模拟 AstrMessageEvent。"""

    def __init__(self, message_str="", origin="aiocqhttp:FriendMessage:1001",
                 sender_id="u1"):
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = sender_id

    def get_sender_id(self):
        return self._sender_id

    def is_private_chat(self):
        return ":FriendMessage:" in f":{self.unified_msg_origin}"

    def plain_result(self, text):
        return text


class FakeMessageChain:
    @staticmethod
    def message(text):
        return text


def _install_fake_astrbot():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")

    api.logger = FakeLogger()
    api.AstrBotConfig = FakeAstrBotConfig
    event.AstrMessageEvent = FakeEvent
    event.MessageChain = FakeMessageChain

    class _Filter:
        def command(self, name, **kw):
            return lambda f: f

        def llm_tool(self, name, **kw):
            return lambda f: f

    event.filter = _Filter()

    class FakeContext:
        def __init__(self):
            self.sent = []

        async def send_message(self, origin, chain):
            self.sent.append((origin, chain))

    class FakeStar:
        def __init__(self, context):
            self.context = context

    def register(*a, **k):
        return lambda cls: cls

    star.Context = FakeContext
    star.Star = FakeStar
    star.register = register

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.star"] = star


def load_main_module():
    _install_fake_astrbot()
    if "main" in sys.modules:
        return sys.modules["main"]
    main = importlib.import_module("main")
    sys.modules["main"] = main
    return main
