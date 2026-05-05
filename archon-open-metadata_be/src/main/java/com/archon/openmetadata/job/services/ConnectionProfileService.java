package com.archon.openmetadata.job.services;

import com.archon.openmetadata.job.models.ConnectionProfile;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface ConnectionProfileService {
  ConnectionProfile save(ConnectionProfile entity);

  ConnectionProfile findById(UUID id);

  List<ConnectionProfile> findAll();

  Page<ConnectionProfile> findAll(Specification<ConnectionProfile> spec, Pageable pageable);

  void deleteById(UUID id);
}
