class Emoji:
    """
    See https://developers.notion.com/reference/emoji-object

    Also wraps Notion's custom (workspace-uploaded) emoji, which the API
    returns as `{"type": "custom_emoji", "custom_emoji": {id, name, url}}`.
    For those, `emoji` is None and `name`/`url` describe the image.
    """

    def __init__(self, client, notion_data):
        self.client = client
        self.type = notion_data["type"]
        if self.type == "custom_emoji":
            custom_emoji = notion_data["custom_emoji"]
            self.emoji = None
            self.name = custom_emoji.get("name")
            self.url = custom_emoji.get("url")
        else:
            self.emoji = notion_data["emoji"]
            self.name = None
            self.url = None
