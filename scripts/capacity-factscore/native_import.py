"""Import unchanged FActScore modules and run a CPU-only synthetic smoke."""
import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath
import platform
import sys
import tarfile
from tempfile import TemporaryDirectory
from unittest.mock import patch

PIN = "f28272deffcf33efc1f1117d5479c10bb75221a9"
DIGEST = "dcc1c9139b6f54ef3d946dc1bbc58b0080092fcbab023890a0728fd17e0eaa4f"
REQUIRED = {"__init__.py", "factscorer.py", "retrieval.py", "abstain_detection.py",
            "atomic_facts.py", "clm.py", "npm.py", "openai_lm.py", "lm.py", "utils.py"}


def verify(condition, message):
    if not condition:
        raise RuntimeError(message)


def materialize_source(archive_path, destination):
    verify(sha256(archive_path.read_bytes()).hexdigest() == DIGEST, "upstream digest")
    package = destination / "factscore"
    package.mkdir()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if len(path.parts) != 3 or path.parts[:2] != ("FActScore-" + PIN, "factscore"):
                continue
            if path.suffix != ".py":
                continue
            name = path.name
            verify(member.isfile() and name not in names and member.size <= 100_000,
                   "unsafe upstream member")
            with archive.extractfile(member) as stream:
                (package / name).write_bytes(stream.read())
            names.add(name)
        verify(REQUIRED <= names, "missing upstream modules")
    return package


def no_network(event, _args):
    if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyaddr", "socket.getnameinfo", "subprocess.Popen",
                 "os.posix_spawn", "os.system", "os.exec"}:
        raise RuntimeError("offline smoke attempted an external operation")


def import_scorer():
    import nltk

    def skip_unused_punkt(resource):
        verify(resource == "punkt", "unexpected NLTK download during FActScore import")
        return False

    # The smoke supplies ready-made assertions and never invokes sentence splitting.
    with patch.object(nltk, "download", side_effect=skip_unused_punkt) as download:
        import factscore.factscorer as scorer
        download.assert_called_once_with("punkt")
    return scorer


class Corpus:
    def get_text_from_title(self, title):
        verify(title == "fictional facility", "wrong topic")
        return [
            {"title": "fictional notice", "text": "Alder plans to open one fictional workshop in 2030."},
            {"title": "fictional annex", "text": "The notice lists no commissioned production line."},
        ]


class Judge:
    def __init__(self):
        self.calls = 0
        self.answer = None

    def generate(self, prompt):
        verify("Alder plans to open one fictional workshop in 2030." in prompt, "no source in prompt")
        verify(self.answer in {"True", "False"}, "unconfigured simulation")
        self.calls += 1
        return self.answer, None

    def save_cache(self):
        pass


def run_synthetic(scorer_type, retrieval_type, root):
    retrieval = retrieval_type(Corpus(), str(root / "cache.json"), str(root / "embeddings.pkl"),
                               retrieval_type="bm25")
    scorer = scorer_type(model_name="npm", cache_dir=str(root), data_dir=str(root),
                         model_dir=str(root), openai_key=str(root / "no-key"))
    scorer.model_name = "retrieval+bohun"
    scorer.retrieval["synthetic"] = retrieval
    judge = Judge()
    scorer.lm = judge
    for claim, expected in [
        ("Alder plans to open a workshop in 2030.", True),
        ("Alder has commissioned the workshop.", False),
    ]:
        judge.answer = "True" if expected else "False"
        result = scorer.get_score("fictional facility", claim, atomic_facts=[[claim]],
                                  knowledge_source="synthetic", gamma=0)
        verify(result["decisions"] == [[{"atom": claim, "is_supported": expected}]],
               "unexpected synthetic verdict")
    verify(judge.calls == 2, "unexpected backend call count")
    return judge.calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    verify(sys.platform == "linux" and platform.machine() == "x86_64"
           and sys.version_info[:2] == (3, 10), "wrong runner")
    sys.addaudithook(no_network)
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        package = materialize_source(args.archive, root)
        sys.path.insert(0, str(root))
        scorer = import_scorer()
        import factscore.retrieval as retrieval
        import torch

        for module in (scorer, retrieval):
            verify(Path(module.__file__).resolve().parent == package, "wrong module origin")
        verify(torch.version.cuda is None, "not a CPU-only torch build")
        simulated = run_synthetic(scorer.FactScorer, retrieval.Retrieval, root)
    print(json.dumps({
        "status": "PASS_NATIVE_IMPORT_SYNTHETIC",
        "pin": PIN,
        "python": platform.python_version(),
        "platform": "linux/x86_64",
        "torch": version("torch"),
        "transformers": version("transformers"),
        "spacy": version("spacy"),
        "bm25": version("rank-bm25"),
        "simulated_calls": simulated,
        "model_api_calls": 0,
        "nltk_punkt_download_suppressed": 1,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
