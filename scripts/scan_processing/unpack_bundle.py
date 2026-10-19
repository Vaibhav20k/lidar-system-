"""Extract a bounded raw bundle without traversal, links or overwriting files."""
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile


def unpack(archive,destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        entries=bundle.infolist()
        if len(entries)>10000 or sum(e.file_size for e in entries)>800*1024*1024:
            raise ValueError('Bundle exceeds extraction limit')
        names=set()
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('Unsafe archive path')
            target=(destination/str(path)).resolve()
            if not target.is_relative_to(destination) or str(target).casefold() in names:
                raise ValueError('Duplicate or escaping path')
            names.add(str(target).casefold())
        for entry in entries:
            target=destination/entry.filename
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with bundle.open(entry) as source,target.open('xb') as out:
                while chunk:=source.read(1024*1024):out.write(chunk)


if __name__=='__main__':unpack(sys.argv[1],sys.argv[2])
