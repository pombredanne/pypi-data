import sqlite3
import tempfile
from pathlib import Path
import tqdm
import click
import polars as pl


@click.command()
@click.argument("sqlite_file", type=click.Path(dir_okay=False))
@click.argument("output_file", type=click.Path(dir_okay=False, exists=False))
def sqlite_to_parquet(sqlite_file, output_file):
    temp_dir = tempfile.TemporaryDirectory()
    temp_dir_path = Path(temp_dir.name)
    with sqlite3.connect(sqlite_file) as conn:
        cursor = conn.cursor()
        items = pl.read_database(
            '''
            select
                project.*,
                url.url,
                url.upload_time,
                url.package_type,
                url.python_version,
                url.requires_python as url_requires_python,
                url.size,
                url.yanked as url_yanked,
                url.yanked_reason as url_yanked_reason
            from urls url
            inner join main.projects project on project.id = url.project_id
            order by url.project_id
            ''',
            cursor,
            iter_batches=True,
            batch_size=500_000
        )
        for idx, item in enumerate(tqdm.tqdm(items)):
            item.write_parquet(
                str(temp_dir_path / f'{idx}.parquet'),
                compression='snappy',
            )
    print('Merging...')
    pl.scan_parquet(f'{temp_dir_path}/*.parquet', cache=False).sink_parquet(
        output_file, compression='zstd', statistics=True,
        row_group_size=50_000
    )
    print('Merged')


if __name__ == '__main__':
    sqlite_to_parquet()
