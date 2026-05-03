package com.archon.openmetadata.iam.services;

import com.archon.openmetadata.iam.models.User;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface UserService {
  User save(User entity);

  User findById(UUID id);

  List<User> findAll();

  Page<User> findAll(Specification<User> spec, Pageable pageable);

  void deleteById(UUID id);
}
