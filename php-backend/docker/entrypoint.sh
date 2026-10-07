#!/bin/sh
set -e
mkdir -p /var/www/html/data/sessions /var/www/html/data/reports
chown -R www-data:www-data /var/www/html/data
exec "$@"
