from tqdm import tqdm
import orjson
import operator
import xmlrpc.client
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict

import backoff
import click
import tqdm
from requests import RequestException, Session

session = Session()
session.headers.update({"User-Agent": "pypi-data (https://github.com/orf/pypi-data)"})


@click.group()
def main():
    pass


@backoff.on_exception(backoff.expo, xmlrpc.client.ProtocolError, max_tries=5)
def get_changelog_since(client, since):
    return client.changelog_since_serial(since)


@main.command()
@click.argument("directory", type=click.Path(file_okay=False))
@click.option("--limit", type=int, default=5_000)
@click.option("--concurrency", type=int)
def download_releases(directory, limit, concurrency):
    directory = Path(directory)
    directory.mkdir(exist_ok=True, parents=True)

    serials_path = directory / "serials.json"
    if serials_path.exists():
        serials = orjson.loads(serials_path.read_bytes())
    else:
        serials = {}

    client = xmlrpc.client.ServerProxy("https://pypi.org/pypi")
    server_packages_by_serial: Dict[str, int] = {
        key.lower(): value
        for key, value in client.list_packages_with_serial().items()
    }

    # Order by oldest updates
    sorted_changes = sorted(
        server_packages_by_serial.items(), key=operator.itemgetter(1)
    )

    changed_packages = [
        (name, serial) for name, serial in sorted_changes if serials.get(name) != serial
    ]
    packages_to_process = changed_packages[0:limit]
    print(
        f"{len(changed_packages)} need refreshing. Processing {len(packages_to_process)} changes now."
    )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        iterator = executor.map(
            lambda v: process_package(directory, *v), packages_to_process
        )

        stats = Counter({"not_found": 0, "releases": 0, "modified": 0, "new": 0})

        for name, serial, skip, package_stats in (
                pbar := tqdm.tqdm(iterator, total=len(packages_to_process))
        ):
            stats.update(package_stats)
            pbar.set_postfix(stats, refresh=False)

            if not skip:
                serials[name.lower()] = serial

    serials_path.write_bytes(orjson.dumps(serials, option=orjson.OPT_SORT_KEYS))


def process_package(directory, name, serial):
    # Sometimes packages cannot be found via the JSON api?
    # https://pypi.org/pypi/jsii-native-python/json for example
    not_found = False
    modified = False
    skip = False
    total_releases = 0
    try:
        package_releases = get_json(f"https://pypi.org/pypi/{name}/json")[
            "releases"
        ].keys()
    except NotFound:
        not_found = True
    else:
        total_releases += len(package_releases)
        releases = {}
        for version in package_releases:
            # django-swstags has a release with '..' as the version. This is obviously not great.
            if version == "..":
                continue
            # It's possible that brand _new_ releases are available from the project /json endpoint, but not
            # currently available from the JSON endpoint (return 404).
            # If this is the case, we break out of the loop and do _skip_ adding this project to the set of
            # "read" projects, as we don't have a complete set of data yet.
            try:
                release_info = get_json(f"https://pypi.org/pypi/{name}/{version}/json")
            except NotFound:
                skip = True
                break
            else:
                releases[version] = release_info
        else:
            lowered_name = name.lower()
            # 1 character release names are apparently OK?
            if len(lowered_name) == 1:
                package_dir = directory / lowered_name
            else:
                package_dir = directory / lowered_name[0] / lowered_name[1]
            package_dir.mkdir(exist_ok=True, parents=True)

            version_file = package_dir / f"{lowered_name}.json"

            modified = version_file.exists()
            if modified:
                previous_data = orjson.loads(version_file.read_bytes())
            else:
                previous_data = {}

            # The "description" is gigantic and often repeated. It's also not really useful. So we just remove it here.
            for idx, release_info in enumerate(reversed(releases.values())):
                release_info["info"].pop("description", "")

            releases = {**previous_data, **releases}
            json_bytes = orjson.dumps(releases, option=orjson.OPT_SORT_KEYS)
            version_file.write_bytes(json_bytes)

    return name, serial, skip, {
        "not_found": not_found,
        "releases": total_releases,
        "modified": modified,
        "new": not not_found and not modified,
        "skipped": skip
    }


class NotFound(Exception):
    pass


@backoff.on_exception(backoff.expo, RequestException, max_tries=5)
def get_json(url):
    res = session.get(url)
    try:
        res.raise_for_status()
    except RequestException as e:
        if e.response.status_code == 404:
            raise NotFound(url) from e
        raise
    return res.json()


if __name__ == "__main__":
    main()
