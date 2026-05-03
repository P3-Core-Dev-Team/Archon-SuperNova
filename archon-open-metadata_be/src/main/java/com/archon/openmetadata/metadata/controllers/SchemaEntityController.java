package com.archon.openmetadata.metadata.controllers;

import com.archon.openmetadata.metadata.dto.SchemaEntityDto;
import com.archon.openmetadata.metadata.dto.SchemaEntityFilterBean;
import com.archon.openmetadata.metadata.models.SchemaEntity;
import com.archon.openmetadata.metadata.services.SchemaEntityService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/schemas")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class SchemaEntityController {

  private final SchemaEntityService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<SchemaEntity> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<SchemaEntityDto>>> fetchAll(Pageable pageable) {
    Page<SchemaEntity> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, SchemaEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(SchemaEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<SchemaEntityDto>> getById(@PathVariable UUID id) {
    SchemaEntity entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, SchemaEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(SchemaEntityController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<SchemaEntityDto>> create(@RequestBody SchemaEntityDto dto) {
    SchemaEntity saved = service.save(modelMapper.map(dto, SchemaEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, SchemaEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(SchemaEntityController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<SchemaEntityDto>> update(
      @PathVariable UUID id, @RequestBody SchemaEntityDto dto) {
    dto.setId(id);
    SchemaEntity updated = service.save(modelMapper.map(dto, SchemaEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, SchemaEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(SchemaEntityController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<SchemaEntityDto>>> searchAll(
      @RequestBody SchemaEntityFilterBean filterBean, Pageable pageable) {
    Page<SchemaEntity> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, SchemaEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(SchemaEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
