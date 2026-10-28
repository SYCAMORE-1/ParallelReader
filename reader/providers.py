"""Translation capabilities, independent of local credentials."""


def text_only(settings):
    return settings.get('translationProvider', 'llm') == 'microsoft'


def review_enabled(settings):
    return not text_only(settings) and bool(settings.get('reviewEnabled'))


def images_enabled(settings):
    return not text_only(settings)


def service_name(settings):
    return 'Azure Translate' if text_only(settings) else settings.get('model', '')
