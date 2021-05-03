import argparse
import sys
import json
import os
from typing import Set, Type, TypeVar, List, Optional, Dict, Callable, Iterable
import collections
import pathlib
import inspect

import attr
import cattr  # type: ignore


T = TypeVar("T")


def interpret_string(s: str, t: Type[T]) -> T:
    try:
        return cattr.structure(s, t)
    except:
        pass
    try:
        return cattr.structure(json.loads(s), t)
    except:
        raise Exception(f"Could not interpret {s} as type {t}")


@attr.s(auto_attribs=True, frozen=True)
class Arguments:
    positional: List[str]
    keyword: Dict[str, str]

    @classmethod
    def from_argv(cls, args: List[str] = sys.argv) -> "Arguments":
        i = 1
        keyword = {}
        positional = []
        while i < len(args):
            arg = args[i]
            key = arg[2:]
            if arg[:2] == "--":
                if len(args) < i+2:
                    raise ValueError(f"Missing value for argument: {key}")
                keyword[key] = args[i + 1]
                i += 2
            else:
                positional.append(arg)
                i += 1
        return cls(positional, keyword)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.keyword.get(key, default)


@attr.s(auto_attribs=True, frozen=True)
class ConfigFiles:
    configs: List[Dict[str, str]]

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        for config in self.configs:
            if key in config:
                return config[key]
        return default


@attr.s(auto_attribs=True, frozen=True)
class Source:
    args: Arguments
    config_files: ConfigFiles
    from_env: bool = True

    def get(self, key: str) -> Optional[str]:
        env_value = os.environ.get(key.upper()) if self.from_env else None
        return self.args.get(key, self.config_files.get(key, env_value))

    @classmethod
    def from_argv(cls, args: List[str] = sys.argv) -> "Source":
        args = Arguments.from_argv(args)
        return cls(args, load_config_files(args.positional))


def load_config_files(filenames: List[str]) -> ConfigFiles:
    # reverse the order so that later config files override earlier ones
    def load(name):
        if not pathlib.Path(name).exists():
            raise ValueError("Could not find config file {name}")
        return json.load(open(name))

    return ConfigFiles([load(name) for name in filenames[::-1]])


def find_obj(t: Type[T], source: Source, prefix: List[str] = []):
    if not attr.has(t):
        raise ValueError(f"{t} is not an attrs class")

    def find(field):
        if field.type is None:
            raise ValueError(f"Field {field.name} of {t} lacks a type annotation")
        return find_value(field.name, field.type, source, prefix)

    d: dict = {field.name: find(field) for field in attr.fields(t)}
    return t(**d)  # type: ignore


def find_value(name, t: Type[T], source: Source, prefix: List[str] = []) -> T:
    prefix = prefix + [name]
    if attr.has(t):
        return find_obj(t, source, prefix)
    prefixed_name = ".".join(prefix)
    value = source.get(prefixed_name)
    if value is None:
        value = os.environ.get(prefixed_name)

    if value is None:
        raise ValueError(f"could not find value for argument {prefixed_name} ({t}")
    return interpret_string(value, t)


def arg_unused(parts: List[str], t: Type[T]) -> bool:
    fields_dict = attr.fields_dict(t)
    if parts[0] not in fields_dict:
        return True
    elif len(parts) == 1:
        return False
    child_type = fields_dict[parts[0]].type
    if child_type is None:
        raise ValueError(f"Missing type annotation for field {child_type} of {t}")
    return arg_unused(parts[1:], child_type)


def check_unused(arg_names: Iterable[str], t: Type[T]) -> Set[str]:
    return {arg for arg in arg_names if arg_unused(arg.split("."), t)}


def build(t: Type[T]) -> T:
    source = Source.from_argv()
    unknown = check_unused(source.args.keyword.keys(), t)
    if unknown:
        print(f"Unknown arguments: {unknown}")
        sys.exit(1)
    return find_obj(t, source)


def run_function(c: Callable[...,T]) -> T:
    source = Source.from_argv()
    def find(name: str, t: Type[T]) -> T:
        if attr.has(t):
            return find_obj(t, source)
        else:
            return find_value(name, t, source)
    args_for_c = {
        name: find(name, parameter.annotation) for name, parameter in inspect.signature(c).parameters.items()
    }
    unused = set(sys.argv[1:])
    for name, value in args_for_c.items():
        if attr.has(value):
            unused = check_unused(unused, type(value))
        else:
            unused -= {name}

    return c(**args_for_c)