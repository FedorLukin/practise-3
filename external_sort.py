"""Внешняя сортировка файлов естественным слиянием."""

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, Sequence

Record = list[str]


def external_natural_merge_sort(
    src: str | os.PathLike | Sequence[str | os.PathLike],
    output: str | os.PathLike | Sequence[str | os.PathLike] = "",
    reverse: bool = False,
    key: str | int | None = "",
    type_data: str = "i",
) -> None:
    """Сортирует один или несколько файлов."""
    sources = _paths(src)
    formats = {path.suffix.lower() for path in sources}
    if len(formats) != 1 or not formats <= {".txt", ".csv"}:
        raise ValueError("Допускаются файлы одного формата: TXT или CSV.")
    file_format = formats.pop()
    type_data = _type_code(type_data)

    if output is None or output == "":
        for source in sources:
            _sort(
                sources=[source],
                target=source,
                reverse=reverse,
                key=key,
                kind=type_data,
                csv_file=file_format == ".csv",
            )
        return

    if isinstance(output, Sequence) and not isinstance(
        output, (str, bytes, os.PathLike)
    ):
        if len(output) != len(sources):
            raise ValueError(
                "Число выходных файлов не совпадает с числом входных."
            )
        targets = [
            Path(item) if item else source
            for source, item in zip(sources, output)
        ]
        for source, target in zip(sources, targets):
            _sort(
                [source],
                target,
                reverse,
                key,
                type_data,
                file_format == ".csv",
            )
        return

    _sort(
        sources, Path(output), reverse, key, type_data, file_format == ".csv"
    )


def my_sort(
    src: str | os.PathLike | Sequence[str | os.PathLike],
    output: str | os.PathLike | Sequence[str | os.PathLike] | None = None,
    reverse: bool = False,
    key: str | int | None = None,
    nflows: int = 1,
    bsize: int | None = None,
) -> None:
    """Сортирует файлы с определением типа данных."""
    if nflows < 1 or (bsize is not None and bsize < 1):
        raise ValueError("nflows и bsize должны быть положительными.")
    external_natural_merge_sort(src, output, reverse, key, "auto")


def _paths(src: str | os.PathLike | Sequence) -> list[Path]:
    paths = (
        [Path(src)]
        if isinstance(src, (str, os.PathLike))
        else [Path(item) for item in src]
    )
    paths = [path.expanduser().absolute() for path in paths]
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError("Исходные файлы не найдены.")
    return paths


def _type_code(value: str) -> str:
    aliases = {
        "i": "i",
        "int": "i",
        "integer": "i",
        "f": "f",
        "float": "f",
        "s": "s",
        "str": "s",
        "string": "s",
        "a": "auto",
        "auto": "auto",
    }
    try:
        return aliases[value.lower()]
    except (AttributeError, KeyError) as error:
        raise ValueError("Тип должен быть i, f, s или auto.") from error


def _sort(
    sources: Sequence[Path],
    target: Path,
    reverse: bool,
    key: str | int | None,
    kind: str,
    csv_file: bool,
) -> None:
    header = _header(sources) if csv_file else None
    column = _column(header, key) if header is not None else None
    records = _records(sources, csv_file, header, column)
    if kind == "auto":
        kind = _infer(sources, csv_file, header, column)

    target = target.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        first, second, merged = [
            Path(folder) / name for name in ("a", "b", "merged")
        ]
        count = _split(records, first, second, csv_file, column, kind, reverse)
        result = first
        while count > 1:
            _merge(first, second, merged, csv_file, column, kind, reverse)
            count = _count_runs(merged, csv_file, column, kind, reverse)
            result = merged
            if count > 1:
                count = _split(
                    _read_tape(merged),
                    first,
                    second,
                    csv_file,
                    column,
                    kind,
                    reverse,
                )
        staged = Path(folder) / "result"
        _write_result(result, staged, csv_file, header)
        os.replace(staged, target)


def _header(sources: Sequence[Path]) -> Record | None:
    expected = None
    for path in sources:
        with path.open(encoding="utf-8-sig", newline="") as file:
            current = next(csv.reader(file), None)
        if current is not None:
            if expected is not None and current != expected:
                raise ValueError("Заголовки CSV-файлов должны совпадать.")
            expected = current
    return expected


def _column(header: Record, key: str | int | None) -> int:
    try:
        index = (
            header.index(key)
            if isinstance(key, str) and key
            else int(key or 0)
        )
    except ValueError as error:
        raise ValueError(f"Столбец {key!r} не найден.") from error
    if not 0 <= index < len(header):
        raise ValueError("Индекс столбца вне диапазона.")
    return index


def _records(
    sources: Sequence[Path],
    csv_file: bool,
    column: int | None,
) -> Iterator[Record]:
    for path in sources:
        with path.open(encoding="utf-8-sig", newline="") as file:
            if not csv_file:
                for line in file:
                    yield [line.rstrip("\r\n")]
                continue
            reader = csv.reader(file)
            next(reader, None)
            for row in reader:
                if row:
                    if column is not None and column >= len(row):
                        raise ValueError(
                            "В строке отсутствует столбец сортировки."
                        )
                    yield row


def _value(record: Record, csv_file: bool, column: int | None) -> str:
    return record[column or 0] if csv_file else record[0]


def _key(value: str, kind: str):
    if kind == "s":
        return value
    if value == "":
        return (1, 0)
    try:
        return (0, int(value) if kind == "i" else float(value))
    except ValueError as error:
        raise ValueError(
            f"Невозможно преобразовать значение {value!r}."
        ) from error


def _infer(
    sources: Sequence[Path],
    csv_file: bool,
    header: Record | None,
    column: int | None,
) -> str:
    integer = floating = True
    found = False
    for row in _records(sources, csv_file, header, column):
        value = _value(row, csv_file, column)
        if not value:
            continue
        found = True
        try:
            int(value)
        except ValueError:
            integer = False
        try:
            float(value)
        except ValueError:
            floating = False
            break
    return "i" if integer and found else "f" if floating and found else "s"


def _dump(file, record: Record | None) -> None:
    file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_tape(path: Path) -> Iterator[Record]:
    with path.open(encoding="utf-8") as file:
        for line in file:
            record = json.loads(line)
            if record is not None:
                yield record
            else:
                yield []


def _split(
    records: Iterable[Record],
    first: Path,
    second: Path,
    csv_file: bool,
    column: int | None,
    kind: str,
    reverse: bool,
) -> int:
    count = 0
    previous = None
    active = 0
    with first.open("w", encoding="utf-8") as file_a, second.open(
        "w", encoding="utf-8"
    ) as file_b:
        files = (file_a, file_b)
        for record in records:
            if not record:
                continue
            current = _key(_value(record, csv_file, column), kind)
            if previous is not None and (
                current > previous if reverse else current < previous
            ):
                _dump(files[active], None)
                active = 1 - active
                previous = None
            if previous is None:
                count += 1
            _dump(files[active], record)
            previous = current
        if previous is not None:
            _dump(files[active], None)
    return count


class _RunReader:
    """Читает серии из временного файла."""

    def __init__(self, path, csv_file, column, kind, reverse):
        self.records = iter(_read_tape(path))
        self.csv_file = csv_file
        self.column = column
        self.kind = kind
        self.reverse = reverse
        self.pending = None

    def next_run(self):
        """Возвращает следующую серию записей."""
        first = self.pending
        self.pending = None
        while first is None or first == []:
            first = next(self.records, None)
            if first is None:
                return None

        def generate():
            previous = _key(
                _value(first, self.csv_file, self.column), self.kind
            )
            yield first
            for record in self.records:
                if not record:
                    break
                current = _key(
                    _value(record, self.csv_file, self.column), self.kind
                )
                unordered = (
                    current > previous if self.reverse else current < previous
                )
                if unordered:
                    self.pending = record
                    return
                previous = current
                yield record

        return generate()


def _merge(
    first: Path,
    second: Path,
    target: Path,
    csv_file: bool,
    column: int | None,
    kind: str,
    reverse: bool,
) -> None:
    readers = [
        _RunReader(path, csv_file, column, kind, reverse)
        for path in (first, second)
    ]
    with target.open("w", encoding="utf-8") as output:
        while True:
            runs = [reader.next_run() for reader in readers]
            if all(run is None for run in runs):
                break
            left = iter(runs[0] or ())
            right = iter(runs[1] or ())
            a, b = next(left, None), next(right, None)
            while a is not None or b is not None:
                if b is None:
                    take_left = True
                elif a is None:
                    take_left = False
                else:
                    left_key = _key(_value(a, csv_file, column), kind)
                    right_key = _key(_value(b, csv_file, column), kind)
                    take_left = (
                        left_key >= right_key
                        if reverse
                        else left_key <= right_key
                    )
                if take_left:
                    _dump(output, a)
                    a = next(left, None)
                else:
                    _dump(output, b)
                    b = next(right, None)
            _dump(output, None)


def _count_runs(
    path: Path, csv_file: bool, column: int | None, kind: str, reverse: bool
) -> int:
    count = 0
    previous = None
    for record in _read_tape(path):
        if not record:
            previous = None
            continue
        current = _key(_value(record, csv_file, column), kind)
        if previous is None or (
            current > previous if reverse else current < previous
        ):
            count += 1
        previous = current
    return count


def _write_result(
    tape: Path,
    target: Path,
    csv_file: bool,
    header: Record | None,
) -> None:
    with target.open("w", encoding="utf-8", newline="") as output:
        if csv_file:
            writer = csv.writer(output)
            if header:
                writer.writerow(header)
            for row in _read_tape(tape):
                if row:
                    writer.writerow(row)
        else:
            for row in _read_tape(tape):
                if row:
                    output.write(row[0] + "\n")


def main() -> None:
    """Запускает сортировку из командной строки."""
    parser = argparse.ArgumentParser(
        description="Внешняя сортировка TXT и CSV."
    )
    parser.add_argument("src", nargs="+")
    parser.add_argument("-o", "--output", default="")
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("-k", "--key", default="")
    parser.add_argument(
        "-t", "--type-data", default="i", choices=("i", "f", "s", "auto")
    )
    args = parser.parse_args()
    external_natural_merge_sort(
        args.src, args.output, args.reverse, args.key, args.type_data
    )


if __name__ == "__main__":
    main()
