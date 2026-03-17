# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from pathlib import Path
from typing import Any, List, Union

import requests

from openjiuwen.core.common.logging import logger

GITHUB_API = "https://api.github.com"
SKILLS_DIR = Path("skills/")
SKILL_FILE_NAME = "SKILL.md"


class GitHubError(Exception):
    pass


class GitHubTree():
    """Represents a Github directory tree with its metadata.

    Attributes:
        repo_owner: The owner of the Github repository.
        repo_name: The name of the Github repository.
        tree_ref: A reference to the root of a Github directory within the repository. 
        Use "HEAD" for the root and the corresponding hash for sub-folders.
        directory: The relative directory (relative to tree_ref) to search as a Path object.
    """

    repo_owner: str
    repo_name: str 
    tree_ref: str 
    directory: Path

    def __init__(
        self, 
        repo_owner: str, 
        repo_name: str, 
        tree_ref: str = "HEAD", 
        directory: Union[str, Path] = Path("")
    ):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.tree_ref = tree_ref
        self.directory = Path(directory)

    def clone(self):
        return GitHubTree(self.repo_owner, self.repo_name, self.tree_ref, self.directory)


class RemoteSkillUtil:
    """Utility class for registering remote skills.

    This class downloads skill directories from GitHub.
    """

    def __init__(self, sys_operation_id: str):
        self._sys_operation_id = sys_operation_id
     
    @property
    def sys_operation_id(self) -> str:
        return self._sys_operation_id
    
    def set_sys_operation_id(self, sys_operation_id: str) -> None:
        self._sys_operation_id = sys_operation_id

    def _get_sys_operation(self) -> Any:
        if not self._sys_operation_id:
            return None
        from openjiuwen.core.runner.runner import Runner
        return Runner.resource_mgr.get_sys_operation(self._sys_operation_id)

    def _recursively_list_github_files(
        self,
        tree: GitHubTree,
        current_directory: Path = Path(""),
        token: str = None,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{GITHUB_API}/repos/{tree.repo_owner}/{tree.repo_name}/git/trees/{tree.tree_ref}"

        relative_directory = tree.directory

        if len(relative_directory.parts) == 0: # Fetch entire tree
            # NOTE: "recursive" only checks whether it is set or not. Remove the param to not use recursive.
            resp = requests.get(url, headers=headers, params={"recursive": "492"})
            data = resp.json()
            if "message" in data:
                raise Exception(data["message"])
            
            files = [
                item
                for item in data.get("tree", [])
                if item["type"] == "blob"
            ]
            for file in files:
                file["path"] = current_directory / file["path"]
            return files, data.get("truncated", False)
        else: # Search for relative_directory
            resp = requests.get(url, headers=headers, params={})
            data = resp.json()
            if "message" in data:
                raise GitHubError(data["message"])
            
            next_directory = relative_directory.parts[0]
            remainder_directory = Path(*relative_directory.parts[1:])

            for item in data.get("tree", []):
                if item["type"] == "tree" and item["path"] == next_directory:
                    new_tree = tree.clone()
                    new_tree.tree_ref = item["sha"]
                    new_tree.directory = remainder_directory
                    return self._recursively_list_github_files(
                        tree=new_tree,
                        current_directory=current_directory / next_directory,
                        token=token
                    )
            raise GitHubError(f"Directory {next_directory} not found in {current_directory}")


    def _list_github_files(
        self,
        tree: GitHubTree,
        token: str = None,
    ):
        if len(tree.directory.parts) > 0 and tree.directory.parts[0] == tree.directory.root:
            tree.directory = Path(*tree.directory.parts[1:])
            
        return self._recursively_list_github_files(
            tree=tree,
            current_directory=Path(""),
            token=token
        )

    @staticmethod
    def download_file_from_github(
        tree: GitHubTree,
        file_path: str,
        token: str = None,
    ):
        headers = {
            "Accept": "application/vnd.github.raw",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"https://api.github.com/repos/{tree.repo_owner}/{tree.repo_name}/contents/{file_path}"
        resp = requests.get(url, headers=headers, params={"ref": tree.tree_ref})

        if resp.status_code != 200:
            raise GitHubError(f"HTTP {resp.status_code} while downloading {file_path}")

        return resp.content

    def search_github_for_skills(
        self,
        tree: GitHubTree,
        token: str = None,
    ):
        files, truncated = self._list_github_files(
            tree,
            token=token
        )
        
        if truncated:
            logger.warning("Warning: file results truncated. Results can be incomplete")
        
        file_list = []
        skill_paths = []

        def _get_path(file) -> Path:
            file_path = file['path']
            if isinstance(file_path, str):
                file_path = Path(file_path)
            return file_path

        def _add_file(file, base_skill_path: Path, parent_directory: Path):
            file_path = _get_path(file)
            file["relative_path"] = base_skill_path / file_path.relative_to(parent_directory)
            file_list.append(file)

        # Files should be sorted after the github query. If it's not sorted, sort it by "path"s
        for i, file in enumerate(files): # O(N) implementation, as the list of files can be up to 100k for large repos
            file_path = _get_path(file)
            if len(file_path.parts) == 1: # Skip if SKILL.md is present in the root directory
                continue
            
            parent_directory = file_path.parent
            file_name = file_path.name
            if file_name != SKILL_FILE_NAME:
                continue

            base_skill_path = Path(parent_directory.name)
            _add_file(file, base_skill_path, parent_directory)
            skill_paths.append(base_skill_path)

            # SKILL.md found, search left & right for other files in the same directory.
            for j in range(i - 1, -1, -1):
                file = files[j]
                file_path = _get_path(file)
                if not file_path.is_relative_to(parent_directory): # No more files in this direction
                    break
                _add_file(file, base_skill_path, parent_directory)
            
            for j in range(i + 1, len(files)):
                file = files[j]
                file_path = _get_path(file)
                if not file_path.is_relative_to(parent_directory): # No more files in this direction
                    break
                _add_file(file, base_skill_path, parent_directory)
            
        return file_list, skill_paths

    async def upload_skill_from_github(
        self,
        tree: GitHubTree,
        skills_dir: str = "",
        token: str = None
    ):
        files, skill_paths = self.search_github_for_skills(
            tree,
            token=token
        )
        for file in files:
            data = self.download_file_from_github(
                tree,
                file_path=file["path"],
                token=token
            )
            relative_path = file["relative_path"]
            logger.info(f"Uploading file to {relative_path}")

            sys_operation = self._get_sys_operation()
            if sys_operation is None:
                return "sys_operation is not available"

            full_path = Path(skills_dir) / relative_path

            fs = sys_operation.fs()
            await fs.write_file(full_path, data, mode="bytes")
            
        return skill_paths