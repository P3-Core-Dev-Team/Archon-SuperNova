package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.repositories.ConnectionProfileRepository;
import com.archon.openmetadata.job.services.ConnectionProfileService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ConnectionProfileServiceImpl implements ConnectionProfileService {
  private final ConnectionProfileRepository repository;

  @Override
  public ConnectionProfile save(ConnectionProfile entity) {
    return repository.save(entity);
  }

  @Override
  public ConnectionProfile findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<ConnectionProfile> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<ConnectionProfile> findAll(Specification<ConnectionProfile> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
