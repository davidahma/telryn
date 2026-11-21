"""Check the draft's source tree and inventory the exact OCI image being signed."""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path


def source(archive, tag, commit):
    prefix = f"telryn-{tag.removeprefix('v')}/"
    tree = subprocess.check_output(["git", "ls-tree", "-rz", commit])
    expected = {}
    for row in tree.rstrip(b"\0").split(b"\0"):
        header, name = row.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("unsupported release source file type")
        expected[prefix + name.decode()] = (mode == "100755", oid)
    algo = subprocess.check_output(
        ["git", "rev-parse", "--show-object-format"], text=True
    ).strip()
    directories = {
        str(parent)
        for name in expected
        for parent in Path(name).parents
        if parent != Path(".")
    }
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if member.mode & 0o7000:
                raise ValueError("special permission bits in source archive")
            if member.isdir() and member.name in directories:
                directories.remove(member.name)
                continue
            if not member.isfile() or member.name not in expected:
                raise ValueError(f"unexpected source member {member.name}")
            executable, oid = expected.pop(member.name)
            data = bundle.extractfile(member).read()
            actual = hashlib.new(
                algo, f"blob {len(data)}\0".encode() + data
            ).hexdigest()
            if executable != bool(member.mode & 0o111) or actual != oid:
                raise ValueError(f"source differs from tagged tree: {member.name}")
    if expected:
        raise ValueError("source archive omitted a tagged file")


def inventory(layout, image, tag, commit, output):
    layout = Path(layout)
    index = json.loads((layout / "index.json").read_bytes())
    (root,) = index["manifests"]

    def blob(descriptor):
        algorithm, value = descriptor["digest"].split(":")
        if (
            algorithm != "sha256"
            or len(value) != 64
            or not all(c in "0123456789abcdef" for c in value)
        ):
            raise ValueError("unexpected OCI digest")
        raw = (layout / "blobs" / algorithm / value).read_bytes()
        if hashlib.sha256(raw).hexdigest() != value or len(raw) != descriptor["size"]:
            raise ValueError("OCI blob differs from its descriptor")
        return json.loads(raw)

    descriptors = blob(root)["manifests"]
    platforms = {}
    for descriptor in descriptors:
        platform = descriptor["platform"]
        arch = platform["architecture"]
        name = f"{platform['os']}/{arch}"
        if name not in {"linux/amd64", "linux/arm64"} or name in platforms:
            raise ValueError("unexpected platform in release image")
        config = blob(blob(descriptor)["config"])
        if (
            config["architecture"] != arch
            or config["os"] != platform["os"]
            or config["config"]["User"] != "65532:65532"
            or config["config"]["Labels"]["org.opencontainers.image.revision"] != commit
        ):
            raise ValueError("image config differs from release scope")
        # Syft gets one explicit platform layout. Every blob still comes from the
        # exact archive users will download, including architecture-specific layers.
        single = layout.parent / arch
        single.mkdir()
        (single / "oci-layout").write_bytes((layout / "oci-layout").read_bytes())
        (single / "blobs").symlink_to(
            (layout / "blobs").resolve(), target_is_directory=True
        )
        (single / "index.json").write_text(
            json.dumps({**index, "manifests": [descriptor]})
        )
        platforms[name] = {
            "digest": descriptor["digest"],
            "sbom": f"telryn-linux-{arch}.spdx.json",
        }
    if len(platforms) != 2:
        raise ValueError("both release platforms are required")
    Path(output).write_text(
        json.dumps(
            {
                "image": image,
                "tag": tag,
                "commit": commit,
                "digest": root["digest"],
                "platforms": platforms,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action", required=True)
    src = commands.add_parser("source")
    for field in ("archive", "tag", "commit"):
        src.add_argument(field)
    oci = commands.add_parser("inventory")
    for field in ("layout", "image", "tag", "commit", "output"):
        oci.add_argument(field)
    args = vars(cli.parse_args())
    action = args.pop("action")
    {"source": source, "inventory": inventory}[action](**args)


if __name__ == "__main__":
    main()
