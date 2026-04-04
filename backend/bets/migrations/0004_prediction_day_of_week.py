from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bets', '0003_prediction_scorecard'),
    ]

    operations = [
        migrations.AddField(
            model_name='prediction',
            name='day_of_week',
            field=models.CharField(
                choices=[
                    ('MON', 'Monday'), ('TUE', 'Tuesday'), ('WED', 'Wednesday'),
                    ('THU', 'Thursday'), ('FRI', 'Friday'), ('SAT', 'Saturday'), ('SUN', 'Sunday'),
                ],
                db_index=True,
                default='SAT',
                max_length=3,
            ),
        ),
    ]
