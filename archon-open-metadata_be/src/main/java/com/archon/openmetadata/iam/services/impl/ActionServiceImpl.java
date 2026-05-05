package com.archon.openmetadata.iam.services.impl;

import com.archon.openmetadata.iam.models.Action;
import com.archon.openmetadata.iam.repositories.ActionRepository;
import com.archon.openmetadata.iam.services.ActionService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ActionServiceImpl implements ActionService {
  private final ActionRepository repository;

  @Override
  public Action save(Action entity) {
    return repository.save(entity);
  }

  @Override
  public Action findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<Action> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<Action> findAll(Specification<Action> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
