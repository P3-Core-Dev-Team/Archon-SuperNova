package com.archon.openmetadata.iam.services.impl;

import com.archon.openmetadata.iam.models.User;
import com.archon.openmetadata.iam.repositories.UserRepository;
import com.archon.openmetadata.iam.services.UserService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class UserServiceImpl implements UserService {
  private final UserRepository repository;

  @Override
  public User save(User entity) {
    return repository.save(entity);
  }

  @Override
  public User findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<User> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<User> findAll(Specification<User> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
