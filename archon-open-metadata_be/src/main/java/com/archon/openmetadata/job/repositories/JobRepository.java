package com.archon.openmetadata.job.repositories;

import com.archon.openmetadata.job.models.Job;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface JobRepository extends JpaRepository<Job, UUID>, JpaSpecificationExecutor<Job> {
    
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {"jobTemplateProfile", "jobTemplateProfile.options"})
    java.util.List<Job> findByStatusIgnoreCase(String status);
}
