package com.archon.openmetadata.job.models;

import com.archon.openmetadata.common.models.AuditModel;
import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import java.util.UUID;
import javax.persistence.*;

import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.hibernate.annotations.GenericGenerator;

@Entity
@JsonIdentityInfo(generator = ObjectIdGenerators.PropertyGenerator.class, property = "id")
@Table(
    name = "job_template_option_rules",
    indexes = {@Index(name = "idx_jtor_operation", columnList = "operation_name")})
@Data
@EqualsAndHashCode(callSuper = true)
@Builder
public class JobTemplateOptionRule extends AuditModel {
  @Id
  @GeneratedValue(generator = "UUID")
  @GenericGenerator(name = "UUID", strategy = "org.hibernate.id.UUIDGenerator")
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY)
  @JoinColumn(name = "job_template_id")
  private JobTemplateProfile jobTemplateProfile;

  @Enumerated(EnumType.STRING)
  @Column(name = "operation_name")
  private OperationType optionType;

  @Column(name = "min_value")
  private Float minValue;

  @Column(name = "max_value")
  private Float maxValue;

  private boolean defaultOption;
}
