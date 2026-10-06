from relay.services.processing import MessageProcessor, TelegramMessage

def test_processing_pipeline_removes_and_replaces_content():
    result = MessageProcessor().process(TelegramMessage(1, 2, "Brand buy OLD https://bad.test\n\n\nnow"), {"remove_urls": True, "remove_source_name": True, "source_name": "Brand", "replacements": [{"from": "OLD", "to": "NEW"}], "prefix": "Update"})
    assert result.accepted
    assert result.text == "Update\nbuy NEW\n\nnow"

def test_filter_and_empty_messages():
    processor = MessageProcessor()
    assert not processor.process(TelegramMessage(1, 1, "contains SPAM"), {"blocked_phrases": ["spam"]}).accepted
    assert processor.process(TelegramMessage(1, 2, "https://x.test"), {"remove_urls": True}).reason == "empty"

