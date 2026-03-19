# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import threading
from typing import Dict, Set, List
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.resources_manager.base import GLOBAL, Tag, TagUpdateStrategy, TagMatchStrategy


class TagMgr:
    def __init__(self):
        self._resource_tags: Dict[str, Set[Tag]] = {}
        self._tag_to_resource: Dict[Tag, Set[str]] = {GLOBAL: set()}

        # Add a lock to ensure atomicity
        self._lock = threading.RLock()  # Reentrant lock, allowing multiple acquisitions by the same thread


    def has_tag(self, tag: Tag) -> bool:
        """Check if a tag exists in the manager"""
        with self._lock:
            return tag in self._tag_to_resource

    def list_tags(self) -> List[Tag]:
        """Get all tags (excluding empty tags)"""
        with self._lock:
            return [tag for tag, resources in self._tag_to_resource.items() if resources]

    def has_resource(self, resource_id) -> bool:
        with self._lock:
            return resource_id in self._resource_tags.keys()

    def tag_resource(self, resource_id: str, tags: List[Tag] | Tag) -> List[Tag]:
        """
        Add tags to a resource (atomic operation)
        """
        tags_to_add = self._normalize_tags(tags)

        with self._lock:
            # Check if resource exists
            if resource_id not in self._resource_tags:
                self._resource_tags.setdefault(resource_id, set())

            # Check if GLOBAL tag is included
            if GLOBAL in tags_to_add:
                old_tags = self._set_global_resource(resource_id)
                logger.info(
                    f"Added GLOBAL tag to resource. resource_id={resource_id}, "
                    f"changed from {old_tags} to [GLOBAL]"
                )
                return [GLOBAL]

            # Add tags
            current_tags = self._add_resource_tags(resource_id, tags_to_add)

            logger.info(
                f"Added tags to resource. resource_id={resource_id}, "
                f"added_tags={tags_to_add}, current_tags={current_tags}"
            )
            return current_tags

    def remove_resource(self, resource_id: str) -> List[Tag]:
        """
        Completely remove a resource and all its tags
        """
        with self._lock:
            if resource_id not in self._resource_tags:
                return []

            removed_tags = self._remove_resource(resource_id)

            logger.info(
                f"Removed resource. resource_id={resource_id}, "
                f"removed_tags={removed_tags}"
            )
            return removed_tags

    def remove_resource_tags(self, resource_id: str, tags: List[Tag] | Tag,
                             skip_if_not_exists: bool = False) -> List[Tag]:
        """
        Remove specified tags from a resource
        """
        tags_to_remove = self._normalize_tags(tags)

        with self._lock:
            if resource_id not in self._resource_tags:
                raise build_error(
                    StatusCode.RESOURCE_TAG_REMOVE_RESOURCE_TAG_ERROR,
                    resource_id=resource_id,
                    tags=tags,
                    reason="Resource does not exist"
                )

            current_tags = self._resource_tags[resource_id]
            
            # 检查是否所有要删除的标签都存在
            if not skip_if_not_exists:
                non_existent_tags = tags_to_remove - current_tags
                if non_existent_tags:
                    raise build_error(
                        StatusCode.RESOURCE_TAG_REMOVE_RESOURCE_TAG_ERROR,
                        resource_id=resource_id,
                        tags=list(non_existent_tags),
                        reason="Tag does not exist"
                    )

            remaining_tags = self._remove_resource_tags(resource_id, tags_to_remove)

            logger.info(
                f"Removed tags from resource. resource_id={resource_id}, "
                f"removed_tags={tags_to_remove}, remaining_tags={remaining_tags}"
            )
            return remaining_tags

    def update_resource_tags(self, resource_id: str, tags: List[Tag] | Tag,
                             tag_update_strategy: TagUpdateStrategy) -> List[Tag]:
        """
        Update resource tags
        """
        new_tags = self._normalize_tags(tags)

        with self._lock:
            if resource_id not in self._resource_tags:
                raise build_error(
                    StatusCode.RESOURCE_TAG_REPLACE_RESOURCE_TAG_ERROR,
                    resource_id=resource_id,
                    tag=tags,
                    reason="Resource does not exist"
                )

            # Check if GLOBAL tag is included
            if GLOBAL in new_tags:
                old_tags = self._set_global_resource(resource_id)
                logger.info(
                    f"Updated resource to GLOBAL. resource_id={resource_id}, "
                    f"strategy={tag_update_strategy}, old_tags={old_tags}"
                )
                return [GLOBAL]

            # Execute operation based on strategy
            if tag_update_strategy == TagUpdateStrategy.REPLACE:
                current_tags = self._replace_resource_tags(resource_id, new_tags)
                logger.info(
                    f"Replaced resource tags. resource_id={resource_id}, "
                    f"new_tags={new_tags}"
                )
            elif tag_update_strategy == TagUpdateStrategy.MERGE:
                current_tags = self._add_resource_tags(resource_id, new_tags)
                logger.info(
                    f"Merged resource tags. resource_id={resource_id}, "
                    f"added_tags={new_tags}, current_tags={current_tags}"
                )
            else:
                raise build_error(
                    StatusCode.RESOURCE_TAG_REPLACE_RESOURCE_TAG_ERROR,
                    resource_id=resource_id,
                    tag=tags,
                    reason=f"Unsupported strategy: {tag_update_strategy}"
                )

            return current_tags

    def remove_tag(self, tag: Tag, skip_if_not_exists: bool = False) -> List[str]:
        """
        Completely remove a tag and all its associations
        """
        with self._lock:
            if tag not in self._tag_to_resource:
                if skip_if_not_exists:
                    return []
                raise build_error(
                    StatusCode.RESOURCE_TAG_REMOVE_TAG_ERROR,
                    tag=tag,
                    reason="Tag does not exist"
                )

            affected_resources = self._remove_tag(tag)

            logger.info(
                f"Removed tag. tag='{tag}', affected_resources={affected_resources}"
            )
            return affected_resources

    def get_tag_resources(self, tag: Tag) -> List[str]:
        """Get all resources with the specified tag"""
        with self._lock:
            return list(self._tag_to_resource.get(tag, set()))

    def find_resources_by_tags(self, tags: List[Tag] | Tag,
                               tag_match_strategy: TagMatchStrategy,
                               skip_if_not_exists: bool = True) -> List[str]:
        """
        Find resources by tags
        """
        tags_to_search = self._normalize_tags(tags)

        with self._lock:
            if tag_match_strategy == TagMatchStrategy.ANY:
                # ANY strategy: resources that have any one of the tags
                found_resources = set()
                for tag in tags_to_search:
                    resources = self._tag_to_resource.get(tag)
                    if not resources:
                        if not self._is_builtin_tag(tag) and not skip_if_not_exists:
                            raise build_error(
                                StatusCode.RESOURCE_TAG_FIND_RESOURCE_ERROR,
                                tag=tags,
                                strategy=tag_match_strategy,
                                reason=f"Tag '{tag}' does not exist"
                            )
                    else:
                        found_resources.update(resources)
                return list(found_resources)

            elif tag_match_strategy == TagMatchStrategy.ALL:
                # ALL strategy: resources that have all tags
                return self._find_resources_with_all_tags(tags_to_search, skip_if_not_exists)

            else:
                raise build_error(
                    StatusCode.RESOURCE_TAG_FIND_RESOURCE_ERROR,
                    tag=tags,
                    strategy=tag_match_strategy,
                    reason="Unsupported tag match strategy"
                )

    def has_resource_tag(self, resource_id: str, tag: Tag) -> bool:
        """Check if a resource has the specified tag"""
        with self._lock:
            return tag in self._resource_tags.get(resource_id, set())

    def get_resources_tags(self, resource_id: str) -> List[Tag]:
        """Get all tags of a resource"""
        with self._lock:
            return list(self._resource_tags.get(resource_id, set()))

    def _set_global_resource(self, resource_id: str) -> List[Tag]:
        """Set resource to GLOBAL tag"""
        # Get old tags
        old_tags = list(self._resource_tags.get(resource_id, set()))

        # Remove old tag associations from _tag_to_resource
        for old_tag in old_tags:
            if old_tag in self._tag_to_resource:
                self._tag_to_resource[old_tag].discard(resource_id)
                # If tag has no resources left, remove the tag (except GLOBAL)
                if not self._tag_to_resource[old_tag] and old_tag != GLOBAL:
                    del self._tag_to_resource[old_tag]

        # Update _resource_tags
        self._resource_tags[resource_id] = {GLOBAL}

        # Update _tag_to_resource
        self._tag_to_resource.setdefault(GLOBAL, set()).add(resource_id)

        return old_tags

    def _add_resource_tags(self, resource_id: str, tags_to_add: Set[Tag]) -> List[Tag]:
        """Add multiple tags to a resource"""
        # Ensure resource exists
        if resource_id not in self._resource_tags:
            return []

        # Check if already a GLOBAL resource
        current_tags = self._resource_tags[resource_id]
        if GLOBAL in current_tags:
            return [GLOBAL]  # GLOBAL resources cannot have other tags

        # Add new tags
        current_tags.update(tags_to_add)

        # Update _tag_to_resource
        for tag in tags_to_add:
            self._tag_to_resource.setdefault(tag, set()).add(resource_id)

        return list(current_tags)

    def _remove_resource(self, resource_id: str) -> List[Tag]:
        """Completely remove a resource"""
        if resource_id not in self._resource_tags:
            return []

        # Get all tags of the resource
        tags = self._resource_tags[resource_id]

        # Remove associations from _tag_to_resource
        for tag in tags:
            if tag in self._tag_to_resource:
                self._tag_to_resource[tag].discard(resource_id)
                # If tag has no resources left, remove the tag (except GLOBAL)
                if not self._tag_to_resource[tag] and tag != GLOBAL:
                    del self._tag_to_resource[tag]

        # Remove resource from _resource_tags
        del self._resource_tags[resource_id]

        return list(tags)

    def _remove_resource_tags(self, resource_id: str, tags_to_remove: Set[Tag]) -> List[Tag]:
        """Remove multiple tags from a resource"""
        if resource_id not in self._resource_tags:
            return []

        current_tags = self._resource_tags[resource_id]

        # Remove specified tags
        for tag in tags_to_remove:
            if tag in current_tags:
                current_tags.discard(tag)
                # Remove association from _tag_to_resource
                if tag in self._tag_to_resource:
                    self._tag_to_resource[tag].discard(resource_id)
                    # If tag has no resources left, remove the tag (except GLOBAL)
                    if not self._tag_to_resource[tag] and tag != GLOBAL:
                        del self._tag_to_resource[tag]

        # If resource has no tags left, remove the resource
        if not current_tags:
            del self._resource_tags[resource_id]

        return list(current_tags)

    def _replace_resource_tags(self, resource_id: str, new_tags: Set[Tag]) -> List[Tag]:
        """Replace all tags of a resource"""
        if resource_id not in self._resource_tags:
            return []

        # Get old tags
        old_tags = self._resource_tags[resource_id]

        # Remove old tag associations from _tag_to_resource
        for old_tag in old_tags:
            if old_tag in self._tag_to_resource:
                self._tag_to_resource[old_tag].discard(resource_id)
                # If tag has no resources left, remove the tag (except GLOBAL)
                if not self._tag_to_resource[old_tag] and old_tag != GLOBAL:
                    del self._tag_to_resource[old_tag]

        # Set new tags
        self._resource_tags[resource_id] = set(new_tags)

        # Update _tag_to_resource
        for tag in new_tags:
            self._tag_to_resource.setdefault(tag, set()).add(resource_id)

        return list(new_tags)

    def _remove_tag(self, tag: Tag) -> List[str]:
        """Completely remove a tag"""
        if tag not in self._tag_to_resource:
            return []

        # Get all resources that have this tag
        affected_resources = list(self._tag_to_resource[tag])

        # Remove the tag from each resource's tag set
        for resource_id in affected_resources:
            if resource_id in self._resource_tags:
                self._resource_tags[resource_id].discard(tag)
                # If resource has no tags left, remove the resource
                if not self._resource_tags[resource_id]:
                    del self._resource_tags[resource_id]

        # Remove tag from _tag_to_resource
        del self._tag_to_resource[tag]

        return affected_resources

    def _find_resources_with_all_tags(self, required_tags: Set[Tag],
                                      skip_if_not_exists: bool) -> List[str]:
        """Find resources that have all specified tags"""
        if not required_tags:
            return []

        # Check if all tags exist
        for tag in required_tags:
            if tag not in self._tag_to_resource:
                if not self._is_builtin_tag(tag) and not skip_if_not_exists:
                    raise build_error(
                        StatusCode.RESOURCE_TAG_FIND_RESOURCE_ERROR,
                        tag=required_tags,
                        strategy=TagMatchStrategy.ALL,
                        reason=f"Tag '{tag}' does not exist"
                    )

        # Get resources of the first tag as initial set
        first_tag = next(iter(required_tags))
        found_resources = set(self._tag_to_resource.get(first_tag, set()))

        # Take intersection: must have all tags
        for tag in required_tags:
            resources = self._tag_to_resource.get(tag, set())
            found_resources.intersection_update(resources)

        return list(found_resources)

    @staticmethod
    def _normalize_tags(tags: List[Tag] | Tag) -> Set[Tag]:
        """Normalize tag input to a set"""
        if isinstance(tags, list):
            return set(tags)
        return {tags}

    @staticmethod
    def _is_builtin_tag(tag: Tag) -> bool:
        """Check if it's a built-in tag"""
        return tag == GLOBAL

    def display(self, enable_log: bool = True) -> str:
        """Display current state"""
        with self._lock:
            msg = '\nTag -> Resource IDs:\n'
            for tag, resource_ids in sorted(self._tag_to_resource.items()):
                if resource_ids:  # Only show tags with resources
                    msg += f"  tag['{tag}']: [{', '.join(sorted(resource_ids))}]\n"

            msg += '\nResource -> Tags:\n'
            for resource_id, tags in sorted(self._resource_tags.items()):
                msg += f"  resource['{resource_id}']: [{', '.join(sorted(tags))}]\n"

            # Statistics
            msg += f"\nStatistics:\n"
            msg += f"  Total tags: {len(self._tag_to_resource)}\n"
            msg += f"  Total resources: {len(self._resource_tags)}\n"
            msg += f"  GLOBAL resources: {len(self._tag_to_resource.get(GLOBAL, set()))}\n"

            if enable_log:
                logger.info(f'---- Tag Manager State ----\n{msg}')

            return msg