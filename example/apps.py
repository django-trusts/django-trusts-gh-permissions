from django.apps import AppConfig


class ExampleConfig(AppConfig):
    name = 'example'
    label = 'example'
    default_auto_field = 'django.db.models.AutoField'
    verbose_name = 'Example user'
