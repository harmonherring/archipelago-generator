#!/bin/sh
# Generate an Archipelago multiworld from a mounted /job directory.
#
# Expected mount layout:
#   /job/Players/        - one or more player YAML configs (required)
#   /job/custom_worlds/  - optional per-job .apworld files
#   /job/output/         - results are written here (AP_<seed>.zip + logs)
set -eu

# Add any per-job apworlds to the baked-in set. Each container is ephemeral,
# so copying into the install dir is safe.
if [ -d /job/custom_worlds ]; then
    for f in /job/custom_worlds/*.apworld; do
        [ -e "$f" ] && cp "$f" "${AP_HOME}/custom_worlds/"
    done
fi

exec "${AP_HOME}/ArchipelagoGenerate" \
    --player_files_path /job/Players \
    --outputpath /job/output \
    "$@"
