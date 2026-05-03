package com.archon.openmetadata.iam.services;

import com.archon.openmetadata.iam.models.Role;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface RoleService {
  Role save(Role entity);

  Role findById(UUID id);

  List<Role> findAll();

  Page<Role> findAll(Specification<Role> spec, Pageable pageable);

  void deleteById(UUID id);
}
