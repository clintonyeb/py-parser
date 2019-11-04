from flask import Flask
from flask import request
import ast
import jsonpickle
from enum import Enum
from collections import defaultdict
import json
import os
import glob
from urllib.parse import urlparse
from git import Repo
import tempfile

from .ignore import files_to_ignore

app = Flask(__name__)


class NodeType(Enum):
    IMPORTS = 'imports'
    CLASSES = 'classes'
    FUNCTIONS = 'functions'
    STATEMENTS = 'statements'


def parse_source_file(file_name, project_name=None):
    # If url, process url first
    if urlparse(file_name).scheme in ('http', 'https'):
        return process_url(file_name)

    # if file path determine if single file or directory
    if os.path.isfile(file_name):
        return process_regular_file(file_name)

    # if directory go through all files recursively
    if os.path.isdir(file_name):
        results = dict()
        if not project_name:
            project_name = os.path.basename(file_name)
        results[project_name] = process_directory(file_name, file_name)
        return results


def process_url(git_url):
    # Download the project locally
    with tempfile.TemporaryDirectory() as temp_dirname:
        Repo.clone_from(git_url, temp_dirname)
        # Run analysis
        return parse_source_file(temp_dirname, git_url.rsplit('/', 1)[-1])


def process_regular_file(file_name):
    if not file_name.endswith('.py'):
        return None

    with open(file_name, "r") as source:
        tree = ast.parse(source.read())

    root_node = defaultdict(list)
    return parse_node(tree, root_node)


def process_directory(root_name, real_name):
    # Correct file_name
    file_name = os.path.join(real_name, '*')

    package_nodes = list()
    for file in glob.iglob(file_name, recursive=True):
        simple_name = os.path.relpath(file, root_name)

        if os.path.isfile(file) and file.endswith('.py'):
            processed_node = process_regular_file(file)
            processed_node['short_name'] = os.path.basename(file)
            processed_node['name'] = simple_name
            processed_node['full_name'] = file
            package_nodes.append(processed_node)

        elif os.path.isdir(file):
            # Ignore some files
            if simple_name not in files_to_ignore:
                child_nodes = process_directory(root_name, file)
                if child_nodes:
                    package_nodes.append({simple_name: child_nodes})

    return package_nodes


def raw_source_file(file_name):
    with open(file_name, "r") as source:
        tree = ast.parse(source.read())
    tree.name = 'app'
    return jsonpickle.encode(tree, unpicklable=False)


def parse_node(ast_node, node):
    analyzer = Analyzer(node)
    analyzer.visit(ast_node)
    return analyzer.node


class Analyzer(ast.NodeVisitor):
    def __init__(self, node):
        self.node = node

    def visit_Import(self, ast_node):
        for alias in ast_node.names:
            self.node[NodeType.IMPORTS.value].append(alias.name)

    def visit_ImportFrom(self, ast_node):
        for alias in ast_node.names:
            self.node[NodeType.IMPORTS.value].append(alias.name)

    def visit_ClassDef(self, ast_node):
        node = defaultdict(list)
        node['name'] = ast_node.name

        node['bases'] = list()
        for base in ast_node.bases:
            if isinstance(base, ast.Name):
                node['bases'].append(base.id)
            elif isinstance(base, ast.Attribute):
                node['bases'].append(base.attr)

        for ast_child_node in ast_node.body:
            parse_node(ast_child_node, node)
        self.node[NodeType.CLASSES.value].append(node)

    def visit_FunctionDef(self, ast_node):
        node = defaultdict(list)
        node['name'] = ast_node.name

        node['args'] = list()
        for arg in ast_node.args.args:
            node['args'].append(arg.arg)

        # node['returns'] = ast_node.returns

        for ast_child_node in ast_node.body:
            parse_node(ast_child_node, node)

        self.node[NodeType.FUNCTIONS.value].append(node)

    def visit_Call(self, ast_node):
        node = dict()
        node['type'] = 'function_call'

        func = ast_node.func

        if isinstance(func, ast.Name):
            node['func'] = func.id
        elif isinstance(func, ast.Attribute):
            node['func'] = func.attr

        node['args'] = list()
        for arg in ast_node.args:
            if isinstance(arg, ast.Name):
                node['args'].append(arg.id)
            elif isinstance(arg, ast.Call):
                call_dict = defaultdict(list)
                parse_node(arg, call_dict)
                node['args'].append(call_dict)
            elif isinstance(arg, ast.Num):
                node['args'].append(arg.n)
            elif isinstance(arg, ast.Str):
                node['args'].append(arg.s)

        node['keywords'] = list()
        for keyword in ast_node.keywords:
            node['keywords'].append(keyword.arg)

        self.node[NodeType.STATEMENTS.value].append(node)

    def visit_Name(self, ast_node):
        node = dict()
        node['name'] = ast_node.id
        node['type'] = ast_node.ctx
        self.node['statements'].append(node)


@app.route('/')
def hello_world():
    return 'Hello I\'m PyParser!'


@app.route('/parse', methods=['POST'])
def parser():
    request_data = request.get_json()
    results = parse_source_file(request_data['fileName'])
    return to_json(results)


@app.route('/raw', methods=['POST'])
def raw():
    request_data = request.get_json()
    results = raw_source_file(request_data['fileName'])
    return to_json(results)


def to_json(data):
    return json.dumps(data, separators=(',', ':'))


if __name__ == '__main__':
    app.run()
