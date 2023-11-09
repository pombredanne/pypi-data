#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

#curl -L https://github.com/pypi-data/pypi-json-data/archive/refs/heads/main.tar.gz | \
#  gtar -xOzf - --wildcards '*.json' | \
#  pv -r -b | \
#  jq -rc 'values[]' | \
#  sqlite3 pypi-data.sqlite ".read scripts/import.sql"

projects_fifo=$(mktemp -d)/projects
urls_fifo=$(mktemp -d)/urls

mkfifo "$projects_fifo"
mkfifo "$urls_fifo"

echo "Creating projects"
libdeflate-gzip -c -d all_data.txt.gz | pv -N projects -c -f -i5 | jq -R -r '. | fromjson | [input_line_number, .info.name, .info.version, .info.author, .info.author_email, .info.home_page,
                                    .info.license, .info.maintainer, .info.maintainer_email, .info.package_url,
                                    .info.platform, .info.project_url, .info.requires_python, .info.summary, if .info.yanked then 1 else 0 end, .info.yanked_reason, (.info.classifiers | tojson), (.info.requires_dist | tojson)] | @csv' > "$projects_fifo" &

echo "Creating urls"
libdeflate-gzip -c -d all_data.txt.gz  | pv -N urls -c -f -i5 | jq -R -r '. | fromjson | .urls[] | [input_line_number, .url, .upload_time_iso_8601, .packagetype, .python_version, .requires_python, .size,
                                                                 if .yanked then 1 else 0 end, .yanked_reason] | @csv' > "$urls_fifo" &

sqlite3 -csv <<EOF
.read scripts/schema.sql
attach database ':memory:' as mydb;
.bail on
.echo on

.import '$projects_fifo' projects
.import '$urls_fifo' urls

select count(*) from projects;
select count(*) from urls;
.save pypi-data.sqlite
EOF

echo "Compressing..."

libdeflate-gzip -1 pypi-data.sqlite
