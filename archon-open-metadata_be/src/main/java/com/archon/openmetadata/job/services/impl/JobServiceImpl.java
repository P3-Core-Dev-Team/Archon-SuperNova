package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.models.Job;
import com.archon.openmetadata.job.repositories.JobRepository;
import com.archon.openmetadata.job.services.JobService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class JobServiceImpl implements JobService {
  private final JobRepository repository;

  @Override
  public Job save(Job entity) {
    return repository.save(entity);
  }

  @Override
  public Job findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<Job> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<Job> findAll(Specification<Job> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
