package com.archon.openmetadata.job.repositories;

import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface JobTemplateOptionRuleRepository
    extends JpaRepository<JobTemplateOptionRule, UUID>,
        JpaSpecificationExecutor<JobTemplateOptionRule> {}
