package com.archon.openmetadata.iam.services.impl;

import com.archon.openmetadata.iam.models.Group;
import com.archon.openmetadata.iam.repositories.GroupRepository;
import com.archon.openmetadata.iam.services.GroupService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class GroupServiceImpl implements GroupService {
  private final GroupRepository repository;

  @Override
  public Group save(Group entity) {
    return repository.save(entity);
  }

  @Override
  public Group findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<Group> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<Group> findAll(Specification<Group> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
