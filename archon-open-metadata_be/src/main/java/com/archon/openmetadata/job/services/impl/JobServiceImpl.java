package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.models.Job;
import com.archon.openmetadata.job.repositories.JobRepository;
import com.archon.openmetadata.job.services.JobService;
import com.archon.openmetadata.metadata.repositories.*;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class JobServiceImpl implements JobService {
  private final JobRepository repository;
  private final RelationshipRepository relationshipRepository;
  private final TableEntityRepository tableRepository;
  private final ColumnEntityRepository columnRepository;
  private final SchemaEntityRepository schemaRepository;
  private final DomainGroupEntityRepository domainGroupRepository;

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
  @Transactional
  public void deleteById(UUID id) {
    // 1. Delete relationships first (references tables and columns)
    relationshipRepository.deleteByJobId(id);
    
    // 2. Delete columns (references tables)
    columnRepository.deleteByJobId(id);
    
    // 3. Delete tables (references schemas and job)
    tableRepository.deleteByJobId(id);
    
    // 4. Delete schemas (references job)
    schemaRepository.deleteByJobId(id);
    
    // 5. Delete domain groups (references job)
    domainGroupRepository.deleteByJobId(id);
    
    // 6. Finally delete the job
    repository.deleteById(id);
  }
}
