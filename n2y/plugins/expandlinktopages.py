from n2y.blocks import LinkToPageBlock

plugin_key = "expandlinktopages"


class ExpandingLinkToPageBlock(LinkToPageBlock):
    """
    Replace page link with the content of the linked-to page.

    """

    def to_pandoc(self):
        assert self.linked_node_id is not None

        if self.link_type != "page_id":
            # TODO: Might be expanded to handle links to databases as well.
            self.client.logger.warning(
                "Links to databases (to:%s from:%s) not supported at this time.",
                self.linked_node_id,
                self.page.notion_id if self.page else None,
            )
            return None

        page = self.client.get_page(self.linked_node_id)
        if page is None:
            msg = "Permission denied when attempting to access linked page (%r)"
            self.client.logger.warning(msg, self.notion_url)
            return None

        # Pages can link to each other (or to themselves); expanding such a
        # cycle would never terminate, so expand each page at most once per
        # chain of expansions.
        expanding = self.client.plugin_data.setdefault(plugin_key, set())
        root_id = self.page.notion_id if self.page is not None else None
        if page.notion_id in expanding or page.notion_id == root_id:
            self.client.logger.warning(
                "Skipping circular link to page %s (%s)",
                page.notion_id,
                self.notion_url,
            )
            return None

        added = {page.notion_id}
        if root_id is not None and root_id not in expanding:
            added.add(root_id)
        expanding.update(added)
        try:
            # Build a fresh block tree for the linked page whose blocks belong
            # to the page being exported (`self.page`), not the linked page,
            # so that e.g. media filenames and page properties come from the
            # exporting document. This deliberately does not touch
            # `page.block`: the linked page's own cached tree must stay bound
            # to the linked page in case it is exported itself later.
            block = self.client.get_block(page.notion_id, page=self.page)
            # `block` is the linked page's ChildPageBlock; we don't want to call
            # `to_pandoc` on it directly, since that yields a full pandoc
            # document, but just the content that would have been in it.
            return block.children_to_pandoc()
        finally:
            expanding.difference_update(added)


notion_classes = {
    "blocks": {
        "link_to_page": ExpandingLinkToPageBlock,
    }
}
