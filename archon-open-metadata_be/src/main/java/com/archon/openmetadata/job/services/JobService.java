package com.archon.openmetadata.job.services;

import com.archon.openmetadata.job.models.Job;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface JobService {
  Job save(Job entity);

  Job findById(UUID id);

  List<Job> findAll();

  Page<Job> findAll(Specification<Job> spec, Pageable pageable);

  void deleteById(UUID id);
}
