package com.archon.openmetadata.iam.services.impl;

import com.archon.openmetadata.iam.models.Role;
import com.archon.openmetadata.iam.repositories.RoleRepository;
import com.archon.openmetadata.iam.services.RoleService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class RoleServiceImpl implements RoleService {
  private final RoleRepository repository;

  @Override
  public Role save(Role entity) {
    return repository.save(entity);
  }

  @Override
  public Role findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<Role> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<Role> findAll(Specification<Role> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
