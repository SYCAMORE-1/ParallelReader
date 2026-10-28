"""Translation capabilities, independent of local credentials."""


def uses_azure(settings):
    return settings.get('translationProvider', 'llm') == 'azure'


def text_only(settings):
    return settings.get('translationProvider', 'llm') in {'azure', 'microsoft'}


def review_enabled(settings):
    return not text_only(settings) and bool(settings.get('reviewEnabled'))


def images_enabled(settings):
    return not text_only(settings)


def service_name(settings):
    return {'azure': 'Azure Translator', 'microsoft': '微软翻译（免配置）'}.get(settings.get('translationProvider'), settings.get('model', ''))
