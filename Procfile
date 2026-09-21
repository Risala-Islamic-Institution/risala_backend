release: python manage.py migrate --noinput
web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --worker-class gthread --max-requests 1000 --max-requests-jitter 100 --timeout 120
