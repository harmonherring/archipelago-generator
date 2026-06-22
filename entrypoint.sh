#!/bin/sh
# Generate an Archipelago multiworld from a mounted /job directory.
#
# Expected mount layout:
#   /job/Players/        - one or more player YAML configs (required)
#   /job/custom_worlds/  - optional per-job .apworld files
#   /job/output/         - results are written here (AP_<seed>.zip + logs)
set -eu

# Copy any user-uploaded apworlds into the install. Each container is ephemeral,
# so copying into the install dir is safe.
if [ -d /job/custom_worlds ]; then
    for f in /job/custom_worlds/*.apworld; do
        [ -e "$f" ] && cp "$f" "${AP_HOME}/custom_worlds/"
    done
fi

# Copy only the library apworlds referenced by the uploaded YAMLs (keeps AP's startup
# world-import fast; baking the whole library into custom_worlds would load all of them).
python3 /usr/local/bin/select_apworlds.py

exec "${AP_HOME}/ArchipelagoGenerate" \
    --player_files_path /job/Players \
    --outputpath /job/output \
    "$@"
